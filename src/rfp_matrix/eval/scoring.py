"""Counts with explicit denominators and adjudicated one-to-one atom matches."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

METHODS = ("B0a", "B0b", "F1", "F2")
ABSTENTIONS = ("cannot_answer_absent", "cannot_answer_conflicting")


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    interval = None
    if denominator:
        z = 1.959963984540054
        p = numerator / denominator
        scale = 1 + z * z / denominator
        midpoint = (p + z * z / (2 * denominator)) / scale
        half = z * math.sqrt((p * (1 - p) + z * z / (4 * denominator)) / denominator) / scale
        interval = [max(0.0, midpoint - half), min(1.0, midpoint + half)]
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None, "wilson_95": interval, "note": "N/A when denominator is zero; item independence is not established."}


def _ids(records: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        key = record["id"]
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError(f"{label} IDs must be unique nonempty strings")
        indexed[key] = record
    return indexed


def _maximum_matching(predicted: Mapping[str, Any], gold: Mapping[str, Any], edges: Sequence[Sequence[str]]) -> list[tuple[str, str]]:
    """Deterministic maximum-cardinality bipartite match, not greedy overlap."""
    adjacency: dict[str, set[str]] = {key: set() for key in predicted}
    for edge in edges:
        if len(edge) != 2:
            raise ValueError("Equivalence edge needs predicted and gold IDs")
        pred_id, gold_id = edge
        if pred_id not in predicted or gold_id not in gold:
            raise ValueError("Equivalence edge references an unknown atom")
        adjacency[pred_id].add(gold_id)
    assigned: dict[str, str] = {}
    by_prediction: dict[str, str] = {}
    for root in sorted(predicted):
        # An iterative augmenting path avoids a recursion ceiling on 2,000 atoms.
        queue = [root]
        seen_predictions = {root}
        preceding: dict[str, str] = {}
        endpoint = None
        for pred_id in queue:
            for gold_id in sorted(adjacency[pred_id]):
                if gold_id in preceding:
                    continue
                preceding[gold_id] = pred_id
                if gold_id not in assigned:
                    endpoint = gold_id
                    break
                displaced = assigned[gold_id]
                if displaced not in seen_predictions:
                    seen_predictions.add(displaced)
                    queue.append(displaced)
            if endpoint is not None:
                break
        while endpoint is not None:
            pred_id = preceding[endpoint]
            old_gold = by_prediction.get(pred_id)
            assigned[endpoint] = pred_id
            by_prediction[pred_id] = endpoint
            endpoint = old_gold
    return sorted((pred_id, gold_id) for gold_id, pred_id in assigned.items())


def _inventory(data: Mapping[str, Any]) -> dict[str, Any]:
    if data.get("equivalence_origin") not in ("human_adjudicated", "hand_oracle"):
        raise ValueError("Atom equivalence must be human-adjudicated or explicitly a hand oracle; overlap is not semantic equivalence")
    gold = _ids(data["gold_atoms"], "gold")
    predicted = _ids(data["predicted_atoms"], "predicted")
    if any(type(item.get("mandatory")) is not bool for item in gold.values()):
        raise ValueError("Gold atoms require an explicit boolean mandatory label")
    matches = _maximum_matching(predicted, gold, data["equivalence_edges"])
    matched_gold = {gold_id for _, gold_id in matches}
    mandatory = {key for key, item in gold.items() if item.get("mandatory") is True}
    misses = [{"id": key, "document_id": item.get("document_id"), "family_id": item.get("family_id"), "page": item.get("page"), "clause": item.get("clause"), "failure_stage": item.get("failure_stage", "inventory_unmatched")} for key, item in sorted(gold.items()) if key in mandatory - matched_gold]
    groups: dict[str, dict[str, Any]] = {}
    for dimension in ("document_id", "family_id"):
        dimension_groups = {}
        labels = sorted({str(item.get(dimension, "unspecified")) for item in gold.values()})
        for label in labels:
            ids = {key for key, item in gold.items() if str(item.get(dimension, "unspecified")) == label}
            dimension_groups[label] = _ratio(len(ids & matched_gold), len(ids))
        groups[dimension] = dimension_groups
    doc_values = [item["value"] for item in groups["document_id"].values() if item["value"] is not None]
    family_values = [item["value"] for item in groups["family_id"].values() if item["value"] is not None]
    return {"precision": _ratio(len(matches), len(predicted)), "recall": _ratio(len(matches), len(gold)), "mandatory_recall": _ratio(len(mandatory & matched_gold), len(mandatory)), "matches": [{"predicted_id": p, "gold_id": g} for p, g in matches], "unmatched_predicted_ids": sorted(set(predicted) - {p for p, _ in matches}), "missed_atom_ids": sorted(set(gold) - matched_gold), "mandatory_misses": misses, "slices": groups, "document_macro_recall": sum(doc_values) / len(doc_values) if doc_values else None, "family_macro_recall": sum(family_values) / len(family_values) if family_values else None, "family_bootstrap_recall": family_bootstrap_recall(list(gold.values()), matched_gold), "matching_policy": "maximum-cardinality over supplied adjudicated edges; stable sorted-ID traversal; each atom used once"}


def family_bootstrap_recall(gold_atoms: Sequence[Mapping[str, Any]], matched_gold_ids: set[str], *, seed: int = 20261023, iterations: int = 1000) -> dict[str, Any]:
    """Bounded cluster resampling of whole families, preserving within-family counts."""
    if not 100 <= iterations <= 2000:
        raise ValueError("Family bootstrap iterations must be between 100 and 2000")
    _ids(gold_atoms, "bootstrap gold")
    counts: dict[str, list[int]] = {}
    for atom in gold_atoms:
        family = atom.get("family_id")
        if not isinstance(family, str) or not family:
            return {"status": "insufficient_family_metadata", "interval_95": None, "seed": seed, "iterations": 0}
        pair = counts.setdefault(family, [0, 0])
        pair[0] += atom["id"] in matched_gold_ids
        pair[1] += 1
    families = sorted(counts)
    if len(families) < 3:
        return {"status": "insufficient_families", "family_count": len(families), "interval_95": None, "seed": seed, "iterations": 0, "note": "At least three labelled families are required; no interval is fabricated."}
    generator = random.Random(seed)
    samples = []
    for _ in range(iterations):
        selected = [counts[generator.choice(families)] for _ in families]
        numerator = sum(pair[0] for pair in selected)
        denominator = sum(pair[1] for pair in selected)
        samples.append(numerator / denominator)
    samples.sort()
    low = samples[max(0, math.ceil(0.025 * iterations) - 1)]
    high = samples[max(0, math.ceil(0.975 * iterations) - 1)]
    return {"status": "estimated_from_supplied_labels", "family_count": len(families), "interval_95": [low, high], "seed": seed, "iterations": iterations, "unstable_small_family_count": len(families) < 6, "note": "Whole-family percentile bootstrap of micro recall. Three-family intervals are unstable; this does not establish generalizability or acceptance."}


def dataset_eligibility(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Report supplied count/prerequisite eligibility; never authenticate custody."""
    if summary is None:
        return {"status": "not_yet_measured", "full_count_eligible": False, "preliminary_count_eligible": False, "acceptance": False}
    full = {"total_documents": 12, "total_families": 6, "total_atoms": 180, "total_mandatory": 60, "development_documents": 6, "development_families": 3, "heldout_documents": 6, "heldout_unseen_families": 3, "heldout_atoms": 90, "heldout_mandatory": 30, "heldout_absent_cases": 20, "heldout_conflict_cases": 10, "heldout_conditional_admin_cases": 20}
    preliminary = {"heldout_documents": 4, "heldout_unseen_families": 3, "heldout_atoms": 60, "heldout_mandatory": 20, "heldout_absent_cases": 10, "heldout_conflict_cases": 5}
    count_keys = set(full) | {"heldout_conditional_cases", "heldout_administrative_cases"}
    for key in count_keys:
        value = summary.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Dataset count {key} must be a nonnegative integer or null")
    reported = {key: summary.get(key) for key in sorted(count_keys)}
    def gaps(floors: Mapping[str, int]) -> list[dict[str, Any]]:
        return [{"field": key, "reported": summary.get(key), "required": required} for key, required in floors.items() if summary.get(key) is None or summary[key] < required]
    full_gaps = gaps(full)
    preliminary_gaps = gaps(preliminary)
    consistency_errors = []
    for total_key, component_keys in (("total_documents", ("development_documents", "heldout_documents")), ("total_families", ("development_families", "heldout_unseen_families")), ("total_atoms", ("heldout_atoms",)), ("total_mandatory", ("heldout_mandatory",))):
        if summary.get(total_key) is not None and all(summary.get(key) is not None for key in component_keys):
            if summary[total_key] < sum(summary[key] for key in component_keys):
                consistency_errors.append(total_key + " is smaller than the reported disjoint subset counts")
    for count_key in ("heldout_mandatory", "heldout_absent_cases", "heldout_conflict_cases", "heldout_conditional_admin_cases", "heldout_conditional_cases", "heldout_administrative_cases"):
        if summary.get(count_key) is not None and summary.get("heldout_atoms") is not None and summary[count_key] > summary["heldout_atoms"]:
            consistency_errors.append(count_key + " exceeds heldout_atoms")
    flags = ("rights_verified", "family_split_before_tuning", "development_families_disjoint", "human_annotated", "independent_mandatory_conflict_adjudication", "independent_custody", "source_config_sealed")
    unmet = [key for key in flags if summary.get(key) is not True]
    slice_counts_present = all(summary.get(key) is not None for key in ("heldout_conditional_cases", "heldout_administrative_cases"))
    return {"status": "reported_counts_only", "counts": reported, "full_count_eligible": not full_gaps and not consistency_errors, "preliminary_count_eligible": not preliminary_gaps and slice_counts_present and not consistency_errors, "full_count_gaps": full_gaps, "preliminary_count_gaps": preliminary_gaps, "reported_consistency_errors": consistency_errors, "preliminary_slice_counts_present": slice_counts_present, "reported_prerequisites_missing": unmet, "reported_dataset_eligible": not full_gaps and not unmet and not consistency_errors, "acceptance": False, "note": "Input mappings do not authenticate rights, adjudication or independent custody. Preliminary evidence is never full acceptance; missing or underfilled slices are insufficient evidence. Actual live arms, hard gates, frozen candidate and independent acceptance remain separate."}


def _retrieval(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    _ids(cases, "retrieval case")
    details = []
    for case in cases:
        relevant = set(case["relevant_ids"])
        retrieved = list(dict.fromkeys(case["retrieved_ids"]))[:10]
        reciprocal_rank = next((1 / rank for rank, key in enumerate(retrieved, 1) if key in relevant), 0.0)
        details.append({"id": case["id"], "relevant_count": len(relevant), "recall_at_10": len(relevant & set(retrieved)) / len(relevant) if relevant else None, "mrr_at_10": reciprocal_rank if relevant else None, "support_recall_at_10": _ratio(len(set(case.get("support_ids", [])) & set(retrieved)), len(set(case.get("support_ids", [])))), "counterevidence_recall_at_10": _ratio(len(set(case.get("counterevidence_ids", [])) & set(retrieved)), len(set(case.get("counterevidence_ids", [])))), "missed_ids": sorted(relevant - set(retrieved))})
    eligible = [row for row in details if row["relevant_count"]]
    return {"eligible_requirement_count": len(eligible), "no_evidence_cases_excluded": len(details) - len(eligible), "recall_at_10": sum(row["recall_at_10"] for row in eligible) / len(eligible) if eligible else None, "mrr_at_10": sum(row["mrr_at_10"] for row in eligible) / len(eligible) if eligible else None, "cases": details, "interval_note": "Recall/MRR are requirement-level macro means; no binomial Wilson interval is claimed for these means."}


def _citations(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    _ids(cases, "citation pair")
    for case in cases:
        if type(case["human_supported"]) is not bool or type(case["structurally_valid"]) is not bool:
            raise ValueError("Citation judgments require explicit human-supported and structurally-valid booleans")
    return {"semantic_precision": _ratio(sum(case["human_supported"] for case in cases), len(cases)), "resolver_validity": _ratio(sum(case["structurally_valid"] for case in cases), len(cases)), "unsupported_pair_ids": [case["id"] for case in cases if not case["human_supported"]], "invalid_pointer_ids": [case["id"] for case in cases if not case["structurally_valid"]], "broad_fallback_count": sum(bool(case.get("broad_fallback", False)) for case in cases), "unit": "atomic answer-claim/cited sentence-or-window pair; literal validity is separate from entailment"}


def _abstention(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    _ids(cases, "abstention case")
    for case in cases:
        if case["gold_status"] not in (*ABSTENTIONS, "supported", "partial") or case["predicted_status"] not in (*ABSTENTIONS, "supported", "partial", "needs_review", "unavailable"):
            raise ValueError("Unknown gold or predicted evidence status")
    results = {}
    for reason in ABSTENTIONS:
        predicted = [case for case in cases if case["predicted_status"] == reason]
        gold = [case for case in cases if case["gold_status"] == reason]
        correct = [case for case in predicted if case["gold_status"] == reason]
        results[reason] = {"precision": _ratio(len(correct), len(predicted)), "coverage": _ratio(len(correct), len(gold)), "false_abstention_ids": [case["id"] for case in predicted if case["gold_status"] != reason], "missed_abstention_ids": [case["id"] for case in gold if case["predicted_status"] != reason]}
    correct_any = sum(case["gold_status"] == case["predicted_status"] and case["gold_status"] in ABSTENTIONS for case in cases)
    results["combined"] = {"precision": _ratio(correct_any, sum(case["predicted_status"] in ABSTENTIONS for case in cases)), "coverage": _ratio(correct_any, sum(case["gold_status"] in ABSTENTIONS for case in cases))}
    results["answered_coverage"] = _ratio(sum(case["predicted_status"] == "supported" for case in cases), len(cases))
    results["unavailable_or_needs_review_count"] = sum(case["predicted_status"] in ("needs_review", "unavailable") for case in cases)
    return results


def _method(data: Mapping[str, Any]) -> dict[str, Any]:
    origin = data.get("origin")
    if origin not in ("hand_oracle", "development"):
        raise ValueError("Public development scoring accepts origin hand_oracle or development only")
    return {"status": "development_scored", "origin": origin, "inventory": _inventory(data["inventory"]), "retrieval": _retrieval(data.get("retrieval_cases", [])), "citations": _citations(data.get("citation_cases", [])), "cannot_answer": _abstention(data.get("abstention_cases", [])), "usage": data.get("usage", {"cost": None, "latency": None, "note": "not measured"}), "model_quality_acceptance": False}


def score_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Score only explicit development inputs; never locate or open private labels."""
    if packet.get("schema_version") != 1 or packet.get("split") != "dev":
        raise ValueError("Expected schema_version=1 and split=dev; held-out scoring requires a separate custodian boundary")
    methods = packet["methods"]
    if not isinstance(methods, Mapping) or set(methods) - set(METHODS):
        raise ValueError("Methods must use B0a/B0b/F1/F2 keys")
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {"schema_version": 1, "split": "dev", "packet_sha256": hashlib.sha256(canonical).hexdigest(), "methods": {method: _method(methods[method]) if method in methods else {"status": "not_yet_measured", "origin": None, "model_quality_acceptance": False} for method in METHODS}, "dataset_eligibility": dataset_eligibility(packet.get("dataset_summary")), "acceptance": "development diagnostics only; runtime, held-out evidence and independent product acceptance remain pending", "family_interval_note": "Seeded bounded family-bootstrap intervals use supplied family labels, require at least three families and retain small-family instability warnings.", "public_quality_claim": False}
