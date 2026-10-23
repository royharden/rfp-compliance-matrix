"""No retrieval score, typed answer or literal quote is itself approval authority."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from rfp_matrix.contracts import (
    INVENTORY_POLICY, M3Error, canonical_json, digest,
    raw_sha256, validate_record,
)
from rfp_matrix.providers import build_questions
from rfp_matrix.source import make_anchor, policy_context, policy_identity, policy_status, resolve_anchor, validate_artifact
from .consumer import ENGINE_POLICY, assessment_generation, assessment_policy, call_judgment, check_provider_sources, judgment_port, provider_identity

METHODS = {"B0a", "B0b", "F1", "F2"}
_MODAL = re.compile(r"\b(shall|must|required|requires|mandatory|submit|submission|deadline|due|provide|include)\b", re.I)
_CONDITION = re.compile(r"\b(if|unless|when|where applicable|provided that|subject to|in the event)\b", re.I)
_ADMIN = re.compile(r"\b(submit|submission|deadline|due|format|page limit|proposal|certification|signature|before|by \d)\b", re.I)
_NEGATION = re.compile(r"\b(no|not|never|cannot|can't|doesn't|without|lack|lacks|unable)\b", re.I)
_STOP = {"a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "be", "is", "are", "will", "shall", "must", "required", "provide", "provides", "include", "includes", "vendor", "supplier", "bidder", "contractor", "we", "our", "it", "that", "this", "with", "have", "has"}


def _method(method: str) -> None:
    if method not in METHODS:
        raise M3Error("unknown_method", "Method must be B0a, B0b, F1 or F2")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text.casefold())


def _core(text: str) -> set[str]:
    return set(_tokens(text)) - _STOP


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _segments(text: str) -> Iterable[tuple[int, int]]:
    """Paragraph/bullet traversal; retain wrapped clauses without offset rewriting."""
    groups: list[tuple[int, int]] = []
    group_start: int | None = None
    group_end = 0
    has_modal = False
    for line in re.finditer(r"[^\n]*(?:\n|$)", text):
        if not line.group().strip():
            if group_start is not None:
                groups.append((group_start, group_end))
            group_start, has_modal = None, False
            continue
        new_modal = bool(_MODAL.search(line.group()))
        new_bullet = bool(re.match(r"\s*(?:[-*•]|\d+[.)])\s", line.group()))
        if group_start is not None and ((has_modal and new_modal) or new_bullet):
            groups.append((group_start, group_end))
            group_start, has_modal = None, False
        if group_start is None:
            group_start = line.start()
        group_end = line.end()
        has_modal |= new_modal
    if group_start is not None:
        groups.append((group_start, group_end))
    for paragraph_start, paragraph_end in groups:
        # Only split boundaries carrying a new explicit obligation. Ambiguous
        # conjunctions remain a flagged human splitting task.
        cuts = [paragraph_start]
        for separator in re.finditer(r"[;.!?]\s+(?=(?:[A-Z][^.;\n]{0,80}\b)?(?:shall|must|submit|provide|include)\b)", text[paragraph_start:paragraph_end], re.I):
            cuts.append(paragraph_start + separator.end())
        cuts.append(paragraph_end)
        for left, right in zip(cuts, cuts[1:]):
            left, right = _trim(text, left, right)
            if left < right:
                yield left, right


def inventory_report(artifact: Mapping[str, Any], *, method: str = "B0a", provider: Any = None,
                     _semantic_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _method(method)
    source = validate_artifact(artifact)
    judgment_provider = judgment_port(provider, method)
    check_provider_sources(judgment_provider, artifact=source)
    semantic = dict(_semantic_context) if _semantic_context is not None else policy_context(method)
    policy_hash = digest("m3.inventory-policy.v4", {"policy": INVENTORY_POLICY, "method": method,
        "typed_threshold": 0.9, "provider": provider_identity(judgment_provider, method), "semantic_identity": policy_identity(semantic)})
    requirements: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for page in source["pages"]:
        for start, end in _segments(page["text"]):
            if len(requirements) + len(rejected) >= 2000:
                raise M3Error("requirement_limit", "Inventory exceeds 2,000 candidates; no partial audit accepted")
            literal = page["text"][start:end]
            section_id = next((s["section_id"] for s in page["sections"] if s["start"] <= start < s["end"]), None)
            anchor = make_anchor(source, page["page_index"], start, end, section_id=section_id)
            resolve_anchor(source, anchor)
            modal = bool(_MODAL.search(literal))
            mandatory = bool(re.search(r"\b(shall|must|required|mandatory)\b", literal, re.I))
            conditional, administrative = bool(_CONDITION.search(literal)), bool(_ADMIN.search(literal))
            uncertainty: list[str] = []
            if not source["complete"]:
                uncertainty.append("source_coverage_incomplete")
            if re.search(r"\b(and|or)\b", literal, re.I) and len(_MODAL.findall(literal)) > 1:
                uncertainty.append("uncertain_atomic_split")
            if page["coverage"] != "ok" or page["issues"]:
                uncertainty.append("page_requires_inspection")
            if method != "B0a":
                questions = build_questions("inventory")
                state = {"clause": literal, "context": page["text"][max(0, start - 200):min(len(page["text"]), end + 200)],
                         "anchor": anchor, "artifact_sha": source["artifact_sha"], "source_sha": source["source"]["raw_sha"],
                         "normalization_policy": source["normalization_policy"]}
                outcome = call_judgment(judgment_provider, state, questions, method=method)
                decisions.append({"anchor": anchor, "outcome": outcome["raw"], "consumer_status": outcome["status"]})
                if outcome["status"] == "labels":
                    labels = outcome["labels"]
                    # F2 makes the same closed decisions without counterfeit scores.
                    # Unknown requirement status retains the candidate for review.
                    if modal and labels["requirement"] is False:
                        uncertainty.append("classification_disagrees_with_source_signal")
                    modal = modal or labels["requirement"] is not False
                    for key in ("requirement", "mandatory", "conditional", "administrative", "atomic"):
                        if labels[key] is None:
                            uncertainty.append(f"uncertain_{key}")
                    mandatory = mandatory or labels["mandatory"] is True
                    conditional = conditional or labels["conditional"] is True
                    administrative = administrative or labels["administrative"] is True
                    uncertainty.append("f2_uncalibrated_classification")
                    if labels["atomic"] is not True:
                        uncertainty.append("uncertain_atomic_split")
                elif outcome["status"] != "ok":
                    uncertainty.append("inventory_provider_unavailable")
                    # Keep uncertain context too: unavailable classification is
                    # never permission to discard a potentially hidden obligation.
                    modal = True
                else:
                    answers = outcome["answers"]
                    p_req = answers["requirement"]["noul"]
                    if modal and p_req < 0.1:
                        uncertainty.append("classification_disagrees_with_source_signal")
                    modal = modal or p_req >= 0.1
                    if 0.1 <= p_req < 0.9:
                        uncertainty.append("uncertain_requirement_classification")
                    mandatory = mandatory or answers["mandatory"]["noul"] >= 0.9
                    conditional = conditional or answers["conditional"]["noul"] >= 0.9
                    administrative = administrative or answers["administrative"]["noul"] >= 0.9
                    for key in ("mandatory", "conditional", "administrative"):
                        if 0.1 < answers[key]["noul"] < 0.9:
                            uncertainty.append(f"uncertain_{key}")
                    if answers["atomic"]["noul"] < 0.9:
                        uncertainty.append("uncertain_atomic_split")
            if not modal:
                rejected.append({"anchor": anchor, "reason": "no_structural_modal_or_submission_signal" if method == "B0a" else "typed_non_requirement",
                                 "method": method, "uncertainty": sorted(set(uncertainty))})
                continue
            requirement_id = "R-" + digest("m3.requirement-id.v1", {"source_id": source["source"]["source_id"], "page": page["page_index"], "start": start, "end": end})[:16]
            value: dict[str, Any] = {"schema_version": 1, "requirement_id": requirement_id, "parent_id": None, "child_ids": [],
                "anchors": [anchor], "literal_clause": literal, "interpretation": literal,
                "mandatory": mandatory, "conditional": conditional, "administrative": administrative,
                "applicability": "unknown" if conditional else "applies", "condition": literal if conditional else None,
                "uncertainty": sorted(set(uncertainty)), "policy_hash": policy_hash, "method": method}
            value["revision"] = digest("m3.requirement-revision.v1", value)
            requirements.append(validate_record("requirement", value))
            if len(requirements) + len(rejected) > 2000:
                raise M3Error("requirement_limit", "Inventory exceeds 2,000 candidates; no partial result accepted")
    return {"schema_version": 1, "artifact_sha": source["artifact_sha"], "requirements": requirements,
            "rejected_candidates": rejected, "judgments": decisions,
            "complete": source["complete"] and all(item["consumer_status"] == "ok" for item in decisions),
            "method": method, "policy_hash": policy_hash, "policy_provenance": semantic}


def inventory_requirements(artifact: Mapping[str, Any], *, method: str = "B0a", provider: Any = None) -> list[dict[str, Any]]:
    return inventory_report(artifact, method=method, provider=provider)["requirements"]


def registered_source_candidates(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """All preclassification candidates, including context later rejected; no port."""
    source = validate_artifact(artifact)
    candidates = []
    for page in source["pages"]:
        for start, end in _segments(page["text"]):
            if len(candidates) >= 2000:
                raise M3Error("requirement_limit", "Candidate freeze exceeds 2,000; no partial registry")
            literal = page["text"][start:end]
            section = next((s["section_id"] for s in page["sections"] if s["start"] <= start < s["end"]), None)
            anchor = make_anchor(source, page["page_index"], start, end, section_id=section)
            resolve_anchor(source, anchor)
            identity = "R-" + digest("m3.requirement-id.v1", {"source_id": source["source"]["source_id"],
                "page": page["page_index"], "start": start, "end": end})[:16]
            candidates.append({"candidate_id": identity, "anchor": anchor, "literal_clause": literal,
                "structural_signal": bool(_MODAL.search(literal)),
                "mandatory": bool(re.search(r"\b(shall|must|required|mandatory)\b", literal, re.I)),
                "conditional": bool(_CONDITION.search(literal)), "administrative": bool(_ADMIN.search(literal)),
                "uncertainty": sorted(set(page["issues"] + ([] if source["complete"] else ["source_coverage_incomplete"])) )})
    content = {"schema_version": 1, "artifact_sha": source["artifact_sha"], "policy": INVENTORY_POLICY,
               "policy_provenance": policy_context("B0a"),
               "candidates": candidates, "complete": source["complete"], "classification_applied": False}
    return {**content, "registry_sha": digest("m3.registered-source-candidates.v1", content)}


def registered_evidence_windows(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze all original registered sentence/windows before method calls."""
    source = _verify_corpus(corpus)
    windows = []
    for record in source["records"]:
        for window in _windows(record):
            if len(windows) >= 20000:
                raise M3Error("window_limit", "Evidence freeze exceeds 20,000 windows; no partial registry")
            pointer = _pointer(source, record, window)
            # _windows/_pointer produce registered original intervals; corpus was
            # already verified. Avoid a full corpus rehash per frozen window.
            if record["text"][pointer["start"]:pointer["end"]] != pointer["quote"]:
                raise M3Error("invalid_evidence", "Registered window literal mismatch")
            windows.append({"record_id": record["record_id"], **window, "pointer": pointer})
    content = {"schema_version": 1, "corpus_generation": source["generation"],
               "policy": "m3-original-sentence-adjacent-windows-v1", "policy_provenance": policy_context("B0a"), "windows": windows,
               "complete": source["complete"]}
    return {**content, "registry_sha": digest("m3.registered-evidence-windows.v1", content)}


def make_corpus(records: Iterable[Mapping[str, Any]], *, complete: bool = True) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        value = dict(record)
        allowed = {"schema_version", "record_id", "text", "terms", "fictional", "source_url"}
        if set(value) - allowed:
            raise M3Error("invalid_corpus", "Unknown vendor record fields", fields=sorted(set(value) - allowed))
        if value.get("schema_version", 1) != 1 or value.get("fictional") is not True:
            raise M3Error("invalid_corpus", "Only explicitly fictional schema-v1 vendor records are accepted")
        identity, text, terms = value.get("record_id"), value.get("text"), value.get("terms")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise M3Error("invalid_corpus", "Vendor record IDs must be nonempty and unique")
        if not isinstance(text, str) or not text.strip() or not isinstance(terms, str) or not terms.strip():
            raise M3Error("invalid_corpus", "Vendor text and declared terms are required")
        seen.add(identity)
        sha = raw_sha256(text.encode("utf-8"))
        item = {"schema_version": 1, "record_id": identity, "text": text, "raw_sha": sha,
                "artifact_sha": digest("m3.vendor-artifact.v1", {"record_id": identity, "raw_sha": sha, "parser": "plain-text-v1"}),
                "terms": terms, "fictional": True, "source_url": value.get("source_url")}
        items.append(item)
        if len(items) > 200 or sum(len(r["text"].encode("utf-8")) for r in items) > 2 * 1024 * 1024:
            raise M3Error("corpus_limit", "Vendor corpus exceeds 200 records / 2 MiB text")
    items.sort(key=lambda item: item["record_id"])
    policy_hash = digest("m3.corpus-policy.v3", {"parser": "plain-text-v1", "fictional_only": True,
                                               "semantic_identity": policy_identity(policy_context("B0a"))})
    content = {"schema_version": 1, "records": items, "complete": complete, "fictional": True,
               "issues": [] if complete else ["corpus_coverage_incomplete"], "policy_hash": policy_hash}
    content["generation"] = digest("m3.corpus-generation.v1", content)
    return validate_record("corpus", content)


def load_corpus(path: str | Path) -> dict[str, Any]:
    supplied = Path(path)
    if supplied.is_symlink() or not supplied.is_file():
        raise M3Error("invalid_path", "Corpus must be a regular non-symlink JSONL file")
    # Metadata contributes bounded overhead beyond the 2MiB text cap.
    if supplied.stat().st_size > 3 * 1024 * 1024:
        raise M3Error("corpus_limit", "Vendor JSONL input exceeds file cap")
    try:
        records = [json.loads(line) for line in supplied.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M3Error("invalid_corpus", "Vendor JSONL cannot be decoded") from exc
    if not all(isinstance(record, dict) for record in records):
        raise M3Error("invalid_corpus", "Every JSONL line must be an object")
    return make_corpus(records)


def _verify_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_record("corpus", corpus)
    content = dict(value)
    expected = content.pop("generation")
    if digest("m3.corpus-generation.v1", content) != expected:
        raise M3Error("corpus_corrupt", "Corpus generation does not match immutable content")
    if sum(len(record["text"].encode("utf-8")) for record in value["records"]) > 2 * 1024 * 1024:
        raise M3Error("corpus_limit", "Immutable corpus exceeds the application text cap")
    seen: set[str] = set()
    for record in value["records"]:
        sha = raw_sha256(record["text"].encode("utf-8"))
        expected_artifact = digest("m3.vendor-artifact.v1", {"record_id": record["record_id"], "raw_sha": sha, "parser": "plain-text-v1"})
        if sha != record["raw_sha"] or expected_artifact != record["artifact_sha"] or record["record_id"] in seen:
            raise M3Error("corpus_corrupt", "Vendor source hash or identity mismatch")
        seen.add(record["record_id"])
    return value


def _windows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = record["text"]
    spans = []
    left = 0
    for boundary in re.finditer(r"[.!?](?:\s+|$)|\n+", text):
        start, end = _trim(text, left, boundary.start() + (1 if text[boundary.start()] in ".!?" else 0))
        if start < end:
            spans.append((start, end))
        left = boundary.end()
    start, end = _trim(text, left, len(text))
    if start < end:
        spans.append((start, end))
    windows = [(start, end, False) for start, end in spans]
    # Adjacent windows retain conditions across sentence boundaries.
    windows += [(first[0], second[1], True) for first, second in zip(spans, spans[1:])]
    return [{"sentence_id": f"{record['record_id']}:{start}:{end}", "start": start, "end": end,
             "quote": text[start:end], "broad_fallback": broad} for start, end, broad in windows]


def _pointer(corpus: Mapping[str, Any], record: Mapping[str, Any], window: Mapping[str, Any]) -> dict[str, Any]:
    quote = window["quote"]
    return validate_record("evidence_pointer", {"schema_version": 1, "record_id": record["record_id"],
        "corpus_generation": corpus["generation"], "source_sha": record["raw_sha"],
        "artifact_sha": record["artifact_sha"], "sentence_id": window["sentence_id"],
        "start": window["start"], "end": window["end"], "quote": quote,
        "excerpt_sha": raw_sha256(quote.encode("utf-8")), "broad_fallback": window["broad_fallback"]})


def resolve_evidence(corpus: Mapping[str, Any], anchor: Mapping[str, Any], *, allow_historical: bool = False) -> dict[str, Any]:
    source = _verify_corpus(corpus)
    pointer = validate_record("evidence_pointer", anchor)
    if pointer["corpus_generation"] != source["generation"]:
        raise M3Error("stale_evidence", "Pointer belongs to another evidence generation")
    record = next((r for r in source["records"] if r["record_id"] == pointer["record_id"]), None)
    if record is None:
        raise M3Error("missing_evidence", "Registered vendor record is missing")
    if record["raw_sha"] != pointer["source_sha"] or record["artifact_sha"] != pointer["artifact_sha"]:
        raise M3Error("stale_evidence", "Vendor artifact identity changed")
    start, end = pointer["start"], pointer["end"]
    if not 0 <= start < end <= len(record["text"]) or record["text"][start:end] != pointer["quote"] or raw_sha256(pointer["quote"].encode("utf-8")) != pointer["excerpt_sha"]:
        raise M3Error("invalid_evidence", "Vendor quote does not resolve literally at original coordinates")
    if allow_historical:
        if pointer["sentence_id"] != f"{record['record_id']}:{start}:{end}":
            raise M3Error("invalid_evidence", "Historical sentence ID is not bound to original registered coordinates")
    else:
        registered = next((w for w in _windows(record) if w["sentence_id"] == pointer["sentence_id"]), None)
        if registered is None or registered["start"] != start or registered["end"] != end or registered["broad_fallback"] != pointer["broad_fallback"]:
            raise M3Error("invalid_evidence", "Sentence/window was not registered")
    return {"quote": pointer["quote"], "context": record["text"][max(0, start - 180):min(len(record["text"]), end + 180)],
            "record_id": record["record_id"], "start": start, "end": end,
            "resolution_mode": "historical_literal_only" if allow_historical else "current_registered_window",
            "semantic_currentness": "not_checked"}


def retrieve_evidence(requirement: Mapping[str, Any], corpus: Mapping[str, Any], *, k: int = 10,
                      _semantic_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if type(k) is not int or not 1 <= k <= 10:
        raise M3Error("candidate_limit", "BM25 candidate cap is 10")
    source = _verify_corpus(corpus)
    semantic = dict(_semantic_context) if _semantic_context is not None else policy_context("B0a")
    query = _tokens(requirement["interpretation"])
    records = source["records"]
    terms = [_tokens(record["text"]) for record in records]
    frequencies = [Counter(tokens) for tokens in terms]
    average = sum(map(len, terms)) / len(terms) if terms else 0
    document_frequency = Counter(term for tokens in terms for term in set(tokens))
    candidates: list[dict[str, Any]] = []
    for record, tokens, frequency in zip(records, terms, frequencies):
        score = 0.0
        for term in set(query):
            count = frequency[term]
            if not count or not average:
                continue
            inverse = math.log(1 + (len(records) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += inverse * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * len(tokens) / average))
        if score > 0:
            candidates.append({"record_id": record["record_id"], "score": score, "windows": _windows(record)})
    candidates.sort(key=lambda row: (-row["score"], row["record_id"]))
    result = candidates[:k]
    for rank, row in enumerate(result, 1):
        row["rank"] = rank
    return {"schema_version": 1, "corpus_generation": source["generation"], "candidates": result,
            "complete": source["complete"], "retrieval_policy": "m3-bm25-k10-v1",
            "policy_hash": digest("m3.retrieval-policy.v3", {"k": k, "algorithm": "m3-bm25-k10-v1", "semantic_identity": policy_identity(semantic)}),
            "policy_provenance": semantic,
            "indexed_records": len(records), "truncated_candidates": max(0, len(candidates) - k)}


def _rule_relation(requirement: Mapping[str, Any], quote: str) -> str:
    core = _core(requirement["interpretation"])
    if not core:
        return "unknown"
    overlap = len(core & _core(quote)) / len(core)
    if overlap < 0.5:
        return "irrelevant"
    if bool(_NEGATION.search(requirement["interpretation"])) != bool(_NEGATION.search(quote)) and overlap >= 0.6:
        return "contradicts"
    # A high lexical match is a DRAFT judgment only; validation remains pending
    # until a human explicitly assesses the exact restricted template.
    if core <= _core(quote) and not _CONDITION.search(quote):
        return "supports"
    return "partial"


def assess_requirement(requirement: Mapping[str, Any], corpus: Mapping[str, Any], *, method: str = "B0a", provider: Any = None,
                       _semantic_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _method(method)
    req, source = validate_record("requirement", requirement), _verify_corpus(corpus)
    port = judgment_port(provider, method)
    semantic = dict(_semantic_context) if _semantic_context is not None else policy_context(method)
    retrieved = retrieve_evidence(req, source, _semantic_context=semantic)
    check_provider_sources(port, corpus=source, requirement=req)
    support: list[dict[str, Any]] = []
    counter: list[dict[str, Any]] = []
    partial = False
    incomplete = (not source["complete"] or bool(req["uncertainty"]) or bool(retrieved["truncated_candidates"])
                  or req["applicability"] == "unknown" or req["method"] != method)
    judgments: list[dict[str, Any]] = []
    origins: set[str] = set()
    model = None
    usages: list[dict[str, Any]] = []
    # Every method keeps original-coordinate, registered-window candidates.
    for candidate in retrieved["candidates"]:
        record = next(r for r in source["records"] if r["record_id"] == candidate["record_id"])
        if method in {"B0a", "B0b"}:
            origins.add("baseline")
            related = [(window, _rule_relation(req, window["quote"])) for window in candidate["windows"]]
            for relation, target in (("contradicts", counter), ("supports", support), ("partial", support)):
                matches = [window for window, result in related if result == relation]
                if matches:
                    window = min(matches, key=lambda item: (item["broad_fallback"], item["end"] - item["start"], item["start"]))
                    pointer = _pointer(source, record, window)
                    resolve_evidence(source, pointer)
                    target.append(pointer)
                    judgments.append({"record_id": record["record_id"], "relation": relation,
                        "origin": "baseline", "sentence_id": window["sentence_id"], "probabilities": None})
            partial |= any(relation == "partial" for _, relation in related)
            incomplete |= any(relation == "unknown" for _, relation in related)
            continue
        windows = candidate["windows"]
        if len(windows) > 254:
            windows = windows[:254]
            incomplete = True
        questions = build_questions("evidence", [window["sentence_id"] for window in windows])
        # Resolve every offered literal before semantic judgment; state carries the
        # exact source/corpus/policy identities used in dispatch/cache identity.
        for window in windows:
            resolve_evidence(source, _pointer(source, record, window))
        state = {"requirement": req["interpretation"], "condition": req["condition"],
            "requirement_revision": req["revision"], "requirement_anchors": req["anchors"],
            "corpus_generation": source["generation"], "vendor_context": record["text"],
            "record_id": record["record_id"], "record_sha": record["raw_sha"],
            "registered_windows": windows, "engine_policy": ENGINE_POLICY}
        outcome = call_judgment(port, state, questions, method=method)
        raw = outcome["raw"]
        judgments.append({"record_id": record["record_id"], "outcome": raw,
                          "consumer_status": outcome["status"], "consumer_reason": outcome.get("reason")})
        raw_origin = raw.get("origin", "unavailable")
        origins.add(raw_origin if raw_origin in {"mock", "replay", "live"} else "unavailable")
        # Usage is retained for unsuccessful/malformed requests too, never only successes.
        if raw.get("usage") is not None:
            usages.append({"record_id": record["record_id"], "origin": raw_origin, "usage": raw["usage"],
                           "accounting": raw.get("accounting"), "cost_status": raw.get("cost_status", "unknown"),
                           "request_hash": raw.get("request_hash")})
        model = raw.get("model") or model
        if outcome["status"] == "unavailable":
            incomplete = True
            continue
        if outcome["status"] == "labels":
            relation, selection = outcome["labels"]["relation"], outcome["labels"]["selection"]
            incomplete = True  # Closed generated labels have no calibrated confidence.
            confident = False
        else:
            relation_answer, selected = outcome["answers"]["relation"], outcome["answers"]["selection"]
            relation, selection = relation_answer["choice"], selected["choice"]
            confident = relation_answer["confidence"] >= 0.9 and selected["confidence"] >= 0.9
        window = next((item for item in windows if item["sentence_id"] == selection), None)
        if relation in {"supports", "contradicts", "partial"} and window is None:
            incomplete = True
            continue
        if window is not None and relation in {"supports", "contradicts", "partial"}:
            pointer = _pointer(source, record, window)
            resolve_evidence(source, pointer)
            if relation == "contradicts":
                counter.append(pointer)  # Never suppress credible counterevidence.
            elif confident or outcome["status"] == "labels":
                support.append(pointer)
            if relation == "partial":
                partial = True
        if not confident or relation == "unknown":
            incomplete = True
    if method in {"F1", "F2"} and not retrieved["candidates"]:
        incomplete = True
        origins.add("unavailable")
    if counter:
        status, reason = "cannot_answer_conflicting", "Conflicting evidence found; incomplete context, if any, remains unresolved"
    elif incomplete:
        status, reason = "needs_review", "Source/corpus coverage, inventory, retrieval or model assessment is incomplete or uncalibrated"
    elif partial:
        status, reason = "partial", "Evidence addresses only part of the requirement; resolve or split before positive approval"
    elif support:
        status, reason = "supported", "Restricted evidence template drafted; explicit human assessment is still required"
    else:
        status, reason = "cannot_answer_absent", "No supporting evidence found in this corpus; this is not proof of vendor incapability"
    origin = next((item for item in ("unavailable", "mock", "replay", "live", "baseline") if item in origins), "baseline")
    value = {"schema_version": 1, "requirement_id": req["requirement_id"], "requirement_revision": req["revision"],
        "corpus_generation": source["generation"], "status": status, "support": support, "counterevidence": counter,
        "candidates": [{key: candidate[key] for key in ("record_id", "score", "rank")} for candidate in retrieved["candidates"]],
        "complete": not incomplete, "reason": reason, "method": method, "origin": origin, "model": model,
        "policy_hash": assessment_policy(method, port, semantic), "policy_provenance": semantic,
        "judgments": judgments, "usage": {"requests": usages} if usages else None,
        "validation": {"schema_version": 1, "state": "pending", "method": "restricted_template", "provenance": origin,
            "reason": "Explicit reviewer assessment must validate the exact claim, literal pointers and applicability"}}
    return validate_record("assessment", value)


def _answer(assessment: Mapping[str, Any]) -> str:
    fixed = {"supported": "Evidence may support this requirement; reviewer assessment is pending.",
             "partial": "Evidence addresses only part of this requirement.",
             "cannot_answer_absent": "No supporting evidence found in this fictional corpus.",
             "cannot_answer_conflicting": "Conflicting evidence prevents a positive answer.",
             "needs_review": "Assessment is incomplete; reviewer attention is required."}[assessment["status"]]
    quoted = [f"{kind} [{pointer['record_id']}:{pointer['start']}:{pointer['end']}]: {pointer['quote']}"
              for kind, pointers in (("Evidence", assessment["support"]), ("Counterevidence", assessment["counterevidence"])) for pointer in pointers]
    return "\n".join([fixed, *quoted, "Fictional evidence only. No autonomous compliance commitment."])


def build_rows(artifact: Mapping[str, Any], corpus: Mapping[str, Any], *, method: str = "B0a", provider: Any = None) -> list[dict[str, Any]]:
    return build_matrix(artifact, corpus, method=method, provider=provider)["rows"]


def build_matrix(artifact: Mapping[str, Any], corpus: Mapping[str, Any], *, method: str = "B0a", provider: Any = None) -> dict[str, Any]:
    """Return rows and discard/uncertainty audit from one inventory assessment."""
    artifact, corpus = validate_artifact(artifact), _verify_corpus(corpus)
    port = judgment_port(provider, method)
    check_provider_sources(port, artifact=artifact, corpus=corpus)
    semantic = policy_context(method)
    report = inventory_report(artifact, method=method, provider=port, _semantic_context=semantic)
    requirements = report["requirements"]
    generation = assessment_generation(artifact["artifact_sha"], corpus["generation"], method, port, semantic)
    rows = []
    for requirement in requirements:
        assessment = assess_requirement(requirement, corpus, method=method, provider=port, _semantic_context=semantic)
        value: dict[str, Any] = {"schema_version": 1, "row_id": requirement["requirement_id"],
            "requirement": requirement, "assessment": assessment, "answer_text": _answer(assessment),
            "validation": assessment["validation"], "assessment_generation": generation,
            "unvalidated_note": ""}
        value["row_revision"] = digest("m3.row-revision.v1", value)
        value["approval"] = "pending"
        value["decision_event_id"] = None
        rows.append(value)
    return {"schema_version": 1, "rows": rows, "inventory_report": report}


def revise_requirements(artifact: Mapping[str, Any], corpus: Mapping[str, Any],
                        rows: list[Mapping[str, Any]], *, operation: str,
                        row_ids: list[str], interpretations: list[str] | None = None,
                        applicability: str | None = None, reason: str,
                        method: str = "B0a", provider: Any = None) -> list[dict[str, Any]]:
    """Construct revisions; D2's transaction owns publication, CAS and history.

    Human interpretations never rewrite literal source strings or coordinates.
    All new answers are rebuilt from the evidence assessment and remain pending.
    Audit ancestry for many-to-one merges is held by the domain revision event;
    the singular parent_id additionally identifies its primary source ancestor.
    """
    _method(method)
    if operation not in {"interpretation", "applicability", "split", "merge"}:
        raise M3Error("invalid_revision", "Unknown requirement revision operation")
    if not reason.strip() or len(reason) > 4000 or not row_ids or len(set(row_ids)) != len(row_ids):
        raise M3Error("invalid_revision", "Revision needs a rationale and distinct source rows")
    selected = []
    for identity in row_ids:
        found = next((row for row in rows if row.get("row_id") == identity), None)
        if found is None:
            raise M3Error("missing_row", "Requirement revision references an unknown row", row_id=identity)
        selected.append(found)
    if operation in {"interpretation", "applicability", "split"} and len(selected) != 1:
        raise M3Error("invalid_revision", "This operation requires exactly one source row")
    if operation == "merge" and len(selected) < 2:
        raise M3Error("invalid_revision", "Merge requires at least two rows")
    if operation == "split" and (not interpretations or len(interpretations) < 2):
        raise M3Error("invalid_revision", "Split requires two or more reviewer-supplied atomic interpretations")
    if operation in {"interpretation", "merge"} and (not interpretations or len(interpretations) != 1):
        raise M3Error("invalid_revision", "Interpretation/merge requires exactly one reviewer interpretation")
    if interpretations is not None and (len(interpretations) > 100 or any(not isinstance(text, str) or not text.strip() or len(text) > 200000 for text in interpretations)):
        raise M3Error("invalid_revision", "Interpretations must be nonempty bounded text")
    if applicability is not None and applicability not in {"applies", "unknown", "not_applies"}:
        raise M3Error("invalid_revision", "Invalid applicability")
    if operation == "applicability" and applicability is None:
        raise M3Error("invalid_revision", "Applicability operation requires an explicit value")
    generations = {row["assessment_generation"] for row in selected}
    if len(generations) != 1 or any(row["assessment"]["corpus_generation"] != corpus["generation"] for row in selected):
        raise M3Error("stale_revision", "Rows no longer share the selected assessment/corpus generation")
    if any(row["assessment"]["method"] != method for row in selected):
        raise M3Error("method_revision_mismatch", "Change methods through full reassessment, not a local row edit")
    revision_port = judgment_port(provider, method)
    check_provider_sources(revision_port, artifact=artifact, corpus=corpus)
    semantic = policy_context(method)
    expected_assessment_policy = assessment_policy(method, revision_port, semantic)
    if any(policy_status(row["assessment"].get("policy_provenance"), semantic)["status"] != "current" for row in selected):
        raise M3Error("stale_revision_policy", "Semantic policy is legacy, missing, changed or restart-required; reassess explicitly")
    # ReviewStore publishes its own authoritative generation after producer rows
    # are materialized; compare producer policy, not that domain generation hash.
    if any(row["assessment"]["policy_hash"] != expected_assessment_policy for row in selected):
        raise M3Error("stale_revision_policy", "Provider/source/policy changed; fully reassess before a local edit")
    originals = [validate_record("requirement", row["requirement"]) for row in selected]
    for requirement in originals:
        for anchor in requirement["anchors"]:
            resolve_anchor(artifact, anchor)
    base = originals[0]
    new_interpretations = interpretations if operation != "applicability" else [base["interpretation"]]
    assert new_interpretations is not None
    new_ids = []
    for index, interpretation in enumerate(new_interpretations):
        if operation in {"interpretation", "applicability"}:
            new_ids.append(base["requirement_id"])
        else:
            new_ids.append("R-" + digest("m3.revised-requirement-id.v1", {"operation": operation,
                "parents": sorted(row_ids), "parent_revisions": [r["revision"] for r in originals],
                "interpretation": interpretation, "index": index, "reason": reason})[:16])
    replacement_rows = []
    for identity, interpretation in zip(new_ids, new_interpretations):
        requirement = dict(base)
        requirement.pop("revision", None)
        if operation == "merge":
            unique = {canonical_json(anchor): anchor for original in originals for anchor in original["anchors"]}
            requirement["anchors"] = list(unique.values())
            requirement["literal_clause"] = "\n".join(anchor["quote"] for anchor in requirement["anchors"])
            requirement["mandatory"] = any(original["mandatory"] for original in originals)
            requirement["conditional"] = any(original["conditional"] for original in originals)
            requirement["administrative"] = any(original["administrative"] for original in originals)
            requirement["uncertainty"] = sorted({flag for original in originals for flag in original["uncertainty"]} - {"uncertain_atomic_split"})
            requirement["condition"] = "\n".join(original["condition"] for original in originals if original["condition"]) or None
            applicability_values = {original["applicability"] for original in originals}
            requirement["applicability"] = base["applicability"] if len(applicability_values) == 1 else "unknown"
        elif operation == "split":
            requirement["uncertainty"] = [flag for flag in requirement["uncertainty"] if flag != "uncertain_atomic_split"]
        requirement["requirement_id"] = identity
        requirement["interpretation"] = interpretation.strip()
        if operation in {"split", "merge"}:
            requirement["parent_id"] = base["requirement_id"]
            requirement["child_ids"] = []
        if applicability is not None:
            requirement["applicability"] = applicability
        requirement["policy_hash"] = digest("m3.human-requirement-revision-policy.v1", {
            "parent_policy": base["policy_hash"], "operation": operation, "reason": reason})
        requirement["revision"] = digest("m3.requirement-revision.v1", requirement)
        requirement = validate_record("requirement", requirement)
        assessment = assess_requirement(requirement, corpus, method=method, provider=revision_port, _semantic_context=semantic)
        row: dict[str, Any] = {"schema_version": 1, "row_id": identity, "requirement": requirement,
            "assessment": assessment, "answer_text": _answer(assessment), "validation": assessment["validation"],
            "assessment_generation": selected[0]["assessment_generation"], "unvalidated_note": ""}
        human_origins = [original["human_origin"] for original in selected if isinstance(original.get("human_origin"), Mapping)]
        if human_origins:
            row["human_origin"] = {**human_origins[0], "derived_operation": operation,
                "derived_from_row_ids": sorted(row_ids), "blind_method_prediction": False,
                "human_source_candidates": [{key: origin.get(key) for key in ("candidate_id", "artifact_sha", "machine_report_sha")}
                                            for origin in human_origins]}
        row["row_revision"] = digest("m3.row-revision.v1", row)
        row["approval"], row["decision_event_id"] = "pending", None
        replacement_rows.append(row)
    return replacement_rows


def reassess_rows(artifact: Mapping[str, Any], new_corpus: Mapping[str, Any],
                  current_rows: list[Mapping[str, Any]], *, method: str = "B0a",
                  provider: Any = None) -> list[dict[str, Any]]:
    """Refresh evidence without undoing the reviewer's active requirement edits.

    Domain update_run publishes the new generation, marks old approvals stale,
    and preserves retired rows/history. No inventory provider is rerun here.
    """
    _method(method)
    source, corpus = validate_artifact(artifact), _verify_corpus(new_corpus)
    port = judgment_port(provider, method)
    check_provider_sources(port, artifact=source, corpus=corpus)
    semantic = policy_context(method)
    generation = assessment_generation(source["artifact_sha"], corpus["generation"], method, port, semantic)
    rows = []
    for current in current_rows:
        if current.get("retired", False):
            continue
        requirement = validate_record("requirement", current["requirement"])
        for anchor in requirement["anchors"]:
            resolve_anchor(source, anchor)
        assessment = assess_requirement(requirement, corpus, method=method, provider=port, _semantic_context=semantic)
        row: dict[str, Any] = {"schema_version": 1, "row_id": current["row_id"], "requirement": requirement,
            "assessment": assessment, "answer_text": _answer(assessment), "validation": assessment["validation"],
            "assessment_generation": generation, "unvalidated_note": current.get("unvalidated_note", "")}
        for lineage_key in ("revision_ancestry", "dependency_revision", "human_origin"):
            if lineage_key in current:
                row[lineage_key] = current[lineage_key]
        row["row_revision"] = digest("m3.row-revision.v1", row)
        row["approval"], row["decision_event_id"] = "pending", None
        rows.append(row)
    return rows
