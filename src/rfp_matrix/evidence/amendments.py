"""Bounded lexical correspondence proposals; never approval or semantic ancestry."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, digest, raw_sha256, validate_record
from rfp_matrix.source import validate_artifact
from .engine import _verify_corpus

POLICY = "m3-amendment-lexical-proposals-v1"
_STOP = {"a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "be", "is", "are", "shall", "must", "vendor", "bidder"}
_ATTRIBUTES = ("mandatory", "conditional", "administrative", "applicability", "condition")


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise M3Error("invalid_amendment_identity", f"{field} must be an exact SHA-256 identity")
    return value


def _tokens(text: str) -> tuple[set[str], bool]:
    tokens: set[str] = set()
    for match in re.finditer(r"[^\W_]+(?:[-./][^\W_]+)*", text.casefold()):
        token = match.group()
        if token not in _STOP:
            tokens.add(token)
            if len(tokens) > 512:
                return set(sorted(tokens)[:512]), True
    return tokens, False


def _snapshot(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, set[str]], bool]:
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("run_id"), str) or not snapshot["run_id"]:
        raise M3Error("invalid_amendment_snapshot", "Comparison needs an owned run snapshot")
    artifact = validate_artifact(snapshot["artifact"])
    corpus = _verify_corpus(snapshot["corpus"])
    raw_rows = snapshot.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) > 2000:
        raise M3Error("amendment_limit", "Comparison accepts at most 2,000 registered rows per snapshot")
    identity = {"artifact_sha": artifact["artifact_sha"], "raw_sha": artifact["source"]["raw_sha"],
        "assessment_generation": _sha(snapshot.get("assessment_generation"), "assessment generation"),
        "matrix_generation": _sha(snapshot.get("matrix_generation"), "matrix generation"),
        "corpus_generation": corpus["generation"]}
    rows = []
    token_sets: dict[str, set[str]] = {}
    seen: set[str] = set()
    text_budget = anchor_budget = 0
    token_incomplete = False
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise M3Error("invalid_amendment_row", "Registered rows must be mappings")
        row_id = row.get("row_id")
        if not isinstance(row_id, str) or not row_id or len(row_id) > 160 or row_id in seen:
            raise M3Error("invalid_amendment_row", "Duplicate, missing or oversized row identity")
        seen.add(row_id)
        req = validate_record("requirement", row["requirement"])
        if req["requirement_id"] != row_id:
            raise M3Error("invalid_amendment_row", "Row and registered requirement identity differ")
        row_revision = _sha(row.get("row_revision"), "row revision")
        _sha(req["revision"], "requirement revision")
        if row.get("assessment_generation") != identity["assessment_generation"] and row.get("retired") is not True:
            raise M3Error("stale_amendment_row", "Active row belongs to another assessment generation")
        text_budget += len(req["literal_clause"]) + len(req["interpretation"]) + len(req["condition"] or "")
        if text_budget > 8_000_000:
            raise M3Error("amendment_limit", "Requirement text exceeds comparison budget; no partial report")
        anchors = []
        unresolved_historical = False
        for position, supplied in enumerate(req["anchors"]):
            anchor_budget += len(supplied["quote"])
            if anchor_budget > 8_000_000:
                raise M3Error("amendment_limit", "Anchor validation exceeds comparison budget")
            anchor = validate_record("anchor", supplied)
            if (anchor["artifact_sha"] != artifact["artifact_sha"] or anchor["source_id"] != artifact["source"]["source_id"]
                    or anchor["source_sha"] != artifact["source"]["raw_sha"] or anchor["normalization_policy"] != artifact["normalization_policy"]
                    or anchor["page_index"] >= len(artifact["pages"])):
                if row.get("retired") is True:
                    unresolved_historical = True
                    continue
                raise M3Error("stale_amendment_anchor", "Correspondence anchor belongs to another immutable source")
            page = artifact["pages"][anchor["page_index"]]
            if page["text"][anchor["start"]:anchor["end"]] != anchor["quote"]:
                raise M3Error("invalid_amendment_anchor", "Original quote does not resolve at registered coordinates")
            if anchor["section_id"] is not None and not any(section["section_id"] == anchor["section_id"] and
                    section["start"] <= anchor["start"] < section["end"] for section in page["sections"]):
                raise M3Error("invalid_amendment_anchor", "Original section identity does not resolve")
            if position < 4:
                context_start = max(0, anchor["start"] - 120)
                context_end = min(len(page["text"]), anchor["start"] + 520)
                anchors.append({key: anchor[key] for key in ("source_id", "source_sha", "artifact_sha", "page_index", "section_id", "start", "end", "excerpt_sha")} | {
                    "quote_excerpt": anchor["quote"][:400], "quote_excerpt_end": min(anchor["end"], anchor["start"] + 400),
                    "quote_truncated": len(anchor["quote"]) > 400, "context": page["text"][context_start:context_end],
                    "context_start": context_start, "context_end": context_end, "context_is_evidence": False})
        tokens, truncated = _tokens(req["literal_clause"] + " " + req["interpretation"])
        token_incomplete |= truncated or not tokens
        if row.get("retired") is not True:
            token_sets[row_id] = tokens
        record = {"row_id": row_id, "row_revision": row_revision, "requirement_id": req["requirement_id"],
            "requirement_revision": req["revision"], "literal_excerpt": req["literal_clause"][:1000],
            "literal_sha": raw_sha256(req["literal_clause"].encode("utf-8")),
            "literal_truncated": len(req["literal_clause"]) > 1000,
            "interpretation_excerpt": req["interpretation"][:1000], "interpretation_truncated": len(req["interpretation"]) > 1000,
            "mandatory": req["mandatory"], "conditional": req["conditional"], "administrative": req["administrative"],
            "applicability": req["applicability"], "condition": req["condition"][:1000] if req["condition"] else None,
            "condition_sha": raw_sha256((req["condition"] or "").encode("utf-8")),
            "condition_truncated": len(req["condition"] or "") > 1000, "uncertainty": req["uncertainty"],
            "parent_id": req["parent_id"], "child_ids": req["child_ids"], "retired": row.get("retired") is True,
            "anchors": anchors, "anchor_count": len(req["anchors"]), "anchors_truncated": len(req["anchors"]) > 4,
            "unresolved_historical_anchors": unresolved_historical,
            "lexical_tokens_truncated": truncated,
            "row_pin": digest("m3.amendment-row.v1", {"row_id": row_id, "row_revision": row_revision,
                        "requirement": req, "retired": row.get("retired") is True})}
        rows.append(record)
    rows.sort(key=lambda row: row["row_id"])
    identity["snapshot_pin"] = digest("m3.amendment-snapshot.v1", {"run_id": snapshot["run_id"],
        "identity": dict(identity), "rows": [{"row_id": row["row_id"], "row_pin": row["row_pin"]} for row in rows]})
    return identity, rows, token_sets, token_incomplete


def amendment_candidates(old_snapshot: Mapping[str, Any], new_snapshot: Mapping[str, Any], *, limit: int = 200) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise M3Error("amendment_limit", "Candidate limit must be an integer in 1..1000")
    old_identity, old_rows, old_tokens, old_incomplete = _snapshot(old_snapshot)
    new_identity, new_rows, new_tokens, new_incomplete = _snapshot(new_snapshot)
    if old_snapshot["run_id"] != new_snapshot["run_id"]:
        raise M3Error("amendment_run_mismatch", "Only historical/current snapshots of the same owned run can be compared")
    limits = {"candidates": limit, "rows_per_snapshot": 2000, "tokens_per_row": 512,
              "posting_rows": 128, "pair_evaluations": 50000, "anchor_previews": 4, "quote_codepoints": 400}
    comparison_id = digest("m3.amendment-comparison.v1", {"run_id": old_snapshot["run_id"],
        "old": old_identity, "new": new_identity, "policy": POLICY, "limits": limits})
    index: dict[str, list[str]] = defaultdict(list)
    for row_id, tokens in sorted(new_tokens.items()):
        for token in sorted(tokens):
            index[token].append(row_id)
    incomplete = old_incomplete or new_incomplete
    old_by_id = {row["row_id"]: row for row in old_rows}
    new_by_id = {row["row_id"]: row for row in new_rows}
    pairs = []
    examined = 0
    old_degree: Counter[str] = Counter()
    new_degree: Counter[str] = Counter()
    for old_id, tokens in sorted(old_tokens.items()):
        neighbors: set[str] = set()
        for token in sorted(tokens):
            posting = index.get(token, [])
            if len(posting) > 128:
                incomplete = True
                continue  # Never let ubiquitous terms cause an unbounded cross product.
            neighbors.update(posting)
        for new_id in sorted(neighbors):
            if examined >= 50000:
                incomplete = True
                break
            examined += 1
            other = new_tokens[new_id]
            union = tokens | other
            score = len(tokens & other) / len(union) if union else 0.0
            if score < 0.15:
                continue
            old_degree[old_id] += 1
            new_degree[new_id] += 1
            pairs.append((round(score, 6), old_id, new_id))
        if examined >= 50000:
            break
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    candidates = []
    for score, old_id, new_id in pairs[:limit]:
        old, new = old_by_id[old_id], new_by_id[new_id]
        reference = {"comparison_id": comparison_id, "old_row_id": old_id, "old_row_revision": old["row_revision"],
                     "new_row_id": new_id, "new_row_revision": new["row_revision"]}
        candidates.append({**reference, "candidate_id": digest("m3.amendment-candidate.v1", reference),
            "lexical_score": score, "score_semantics": "token_overlap_not_equivalence", "confirmed": False,
            "field_changes": {field: {"old": old[field], "new": new[field]} for field in _ATTRIBUTES
                              if (old[field] != new[field] if field != "condition" else old["condition_sha"] != new["condition_sha"])},
            "literal_changed": old["literal_sha"] != new["literal_sha"],
            "ambiguity": {"old_degree": old_degree[old_id], "new_degree": new_degree[new_id],
                "split_possible": old_degree[old_id] > 1, "merge_possible": new_degree[new_id] > 1,
                "degrees_are_lower_bounds": incomplete}, "ancestry": "unknown_until_human_correspondence"})
    displayed_old = {item["old_row_id"] for item in candidates}
    displayed_new = {item["new_row_id"] for item in candidates}
    unmapped_old = sorted(set(old_tokens) - displayed_old)
    unmapped_new = sorted(set(new_tokens) - displayed_new)
    content = {"schema_version": 1, "policy": POLICY, "run_id": old_snapshot["run_id"],
        "comparison_id": comparison_id, "old_identity": old_identity, "new_identity": new_identity,
        "old_rows": old_rows, "new_rows": new_rows, "candidates": candidates,
        "counts": {"old_active": len(old_tokens), "new_active": len(new_tokens), "candidate_pairs": len(pairs),
            "displayed_candidates": len(candidates), "unmapped_old": len(unmapped_old), "unmapped_new": len(unmapped_new),
            "removed": sum(not old_degree[row_id] for row_id in old_tokens), "added": sum(not new_degree[row_id] for row_id in new_tokens)},
        "unmapped_old_ids": unmapped_old, "unmapped_new_ids": unmapped_new,
        "unmapped_mandatory_old_ids": [row_id for row_id in unmapped_old if old_by_id[row_id]["mandatory"]],
        "unmapped_mandatory_new_ids": [row_id for row_id in unmapped_new if new_by_id[row_id]["mandatory"]],
        "truncated": len(pairs) > limit, "omitted_candidates": max(0, len(pairs) - limit),
        "retrieval_incomplete": incomplete, "pairs_examined": examined, "limits": limits,
        "added_removed_semantics": "unmapped_lexical_proposals_not_confirmed_additions_or_removals",
        "approval_carry_allowed": False, "new_approvals_require_review": True,
        "ancestry": "unknown_until_human_correspondence", "human_events": []}
    return {**content, "report_sha": digest("m3.amendment-report.v1", content)}


def validate_correspondence_selection(comparison: Mapping[str, Any], *, comparison_id: str,
        expected_old_identity: Mapping[str, Any], expected_new_identity: Mapping[str, Any], relation: str,
        old_row_revisions: Mapping[str, str], new_row_revisions: Mapping[str, str]) -> dict[str, Any]:
    """Pure membership/pin preflight; the review store owns authenticated durable CAS and events.

    A successful selection only proposes lineage. It changes no row, approval,
    source, report or review requirement and authenticates no caller/actor.
    """
    if not isinstance(expected_old_identity, Mapping) or not isinstance(expected_new_identity, Mapping):
        raise M3Error("stale_correspondence", "Expected identities must be closed source-generation mappings")
    if (comparison_id != comparison.get("comparison_id") or dict(expected_old_identity) != comparison.get("old_identity")
            or dict(expected_new_identity) != comparison.get("new_identity")):
        raise M3Error("stale_correspondence", "Comparison/artifact/generation identities changed")
    if not isinstance(old_row_revisions, Mapping) or not isinstance(new_row_revisions, Mapping):
        raise M3Error("invalid_correspondence", "Selection must use registered row/revision mappings")
    counts = (len(old_row_revisions), len(new_row_revisions))
    valid = {"one_to_one": counts == (1, 1), "split": counts[0] == 1 and 2 <= counts[1] <= 100,
             "merge": 2 <= counts[0] <= 100 and counts[1] == 1,
             "added": counts[0] == 0 and 1 <= counts[1] <= 100,
             "removed": 1 <= counts[0] <= 100 and counts[1] == 0}
    if not isinstance(relation, str) or not valid.get(relation, False):
        raise M3Error("invalid_correspondence", "Relation cardinality is inconsistent with registered selections")
    for side, selected in (("old", old_row_revisions), ("new", new_row_revisions)):
        registered = {row["row_id"]: row for row in comparison[side + "_rows"]}
        for row_id, revision in selected.items():
            row = registered.get(row_id)
            if row is None or row["retired"] or revision != row["row_revision"]:
                raise M3Error("stale_correspondence_row", "Unknown, retired or changed correspondence row")
    return {"comparison_id": comparison_id, "relation": relation,
            "old_row_revisions": dict(sorted(old_row_revisions.items())),
            "new_row_revisions": dict(sorted(new_row_revisions.items())),
            "approval_carry_allowed": False, "human_confirmation_required": True}
