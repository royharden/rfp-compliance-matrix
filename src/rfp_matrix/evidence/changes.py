"""Bounded deterministic change context; never a semantic-equivalence judgment."""
from __future__ import annotations

from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, digest
from rfp_matrix.source import validate_artifact
from .engine import _verify_corpus


def describe_changes(old_artifact: Mapping[str, Any], new_artifact: Mapping[str, Any],
                     old_corpus: Mapping[str, Any], new_corpus: Mapping[str, Any], *,
                     limit: int = 500, quote_limit: int = 400) -> dict[str, Any]:
    """Return explanatory deltas, not permission to retain any approval.

    Page/section comparisons are positional. Changed raw sources have unknown
    ancestry even when IDs or strings match. Whole-corpus generation changes
    invalidate all dependent rows, including those whose cited record is unchanged.
    `limit` bounds displayed entries globally; complete counts/digests remain.
    """
    if type(limit) is not int or not 1 <= limit <= 2000 or type(quote_limit) is not int or not 0 <= quote_limit <= 2000:
        raise M3Error("change_context_limit", "Invalid change-context display bounds")
    before, after = validate_artifact(old_artifact), validate_artifact(new_artifact)
    previous, current = _verify_corpus(old_corpus), _verify_corpus(new_corpus)
    entries: list[dict[str, Any]] = []
    counts = {"pages_added": 0, "pages_removed": 0, "pages_changed": 0,
              "sections_added": 0, "sections_removed": 0, "sections_changed": 0,
              "records_added": 0, "records_removed": 0, "records_changed": 0}
    omitted = 0

    def emit(kind: str, value: dict[str, Any]) -> None:
        nonlocal omitted
        counts[kind] += 1
        if len(entries) < limit:
            entries.append({"kind": kind, **value})
        else:
            omitted += 1

    def span(artifact: Mapping[str, Any], page: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
        excerpt_end = min(end, start + quote_limit)
        return {"artifact_sha": artifact["artifact_sha"], "source_sha": artifact["source"]["raw_sha"],
                "page_index": page["page_index"], "page_text_sha": page["text_sha"],
                "start": start, "end": excerpt_end, "quote": page["text"][start:excerpt_end],
                "full_span_end": end, "quote_truncated": excerpt_end < end}

    for index in range(max(len(before["pages"]), len(after["pages"]))):
        left = before["pages"][index] if index < len(before["pages"]) else None
        right = after["pages"][index] if index < len(after["pages"]) else None
        left_hash = digest("m3.coverage-page.v1", left) if left is not None else None
        right_hash = digest("m3.coverage-page.v1", right) if right is not None else None
        if left_hash != right_hash:
            kind = "pages_added" if left is None else ("pages_removed" if right is None else "pages_changed")
            emit(kind, {"page_index": index, "old_sha": left_hash, "new_sha": right_hash,
                        "old": span(before, left, 0, len(left["text"])) if left is not None else None,
                        "new": span(after, right, 0, len(right["text"])) if right is not None else None,
                        "comparison": "positional_only_not_ancestry"})
        old_sections = {item["section_id"]: item for item in left["sections"]} if left is not None else {}
        new_sections = {item["section_id"]: item for item in right["sections"]} if right is not None else {}
        for identity in sorted(set(old_sections) | set(new_sections)):
            old, new = old_sections.get(identity), new_sections.get(identity)
            old_sha = digest("m3.section-content.v1", {"section": old, "text": left["text"][old["start"]:old["end"]]}) if old is not None else None
            new_sha = digest("m3.section-content.v1", {"section": new, "text": right["text"][new["start"]:new["end"]]}) if new is not None else None
            if old_sha == new_sha:
                continue
            kind = "sections_added" if old is None else ("sections_removed" if new is None else "sections_changed")
            emit(kind, {"page_index": index, "section_id": identity, "old_sha": old_sha, "new_sha": new_sha,
                        "old": span(before, left, old["start"], old["end"]) if old is not None else None,
                        "new": span(after, right, new["start"], new["end"]) if new is not None else None,
                        "comparison": "registered_id_positional_only_not_ancestry"})
    old_records = {item["record_id"]: item for item in previous["records"]}
    new_records = {item["record_id"]: item for item in current["records"]}
    record_ids = {"added": [], "removed": [], "changed": []}
    for identity in sorted(set(old_records) | set(new_records)):
        old, new = old_records.get(identity), new_records.get(identity)
        old_sha = digest("m3.record-change.v1", old) if old is not None else None
        new_sha = digest("m3.record-change.v1", new) if new is not None else None
        if old_sha == new_sha:
            continue
        disposition = "added" if old is None else ("removed" if new is None else "changed")
        record_ids[disposition].append(identity)
        emit("records_" + disposition, {"record_id": identity, "old_sha": old_sha, "new_sha": new_sha,
            "old_raw_sha": old["raw_sha"] if old is not None else None,
            "new_raw_sha": new["raw_sha"] if new is not None else None,
            "old_artifact_sha": old["artifact_sha"] if old is not None else None,
            "new_artifact_sha": new["artifact_sha"] if new is not None else None,
            "old_quote": old["text"][:quote_limit] if old is not None else None,
            "new_quote": new["text"][:quote_limit] if new is not None else None,
            "quote_start": 0, "quote_limit": quote_limit,
            "old_quote_end": min(len(old["text"]), quote_limit) if old is not None else None,
            "new_quote_end": min(len(new["text"]), quote_limit) if new is not None else None,
            "old_quote_truncated": len(old["text"]) > quote_limit if old is not None else False,
            "new_quote_truncated": len(new["text"]) > quote_limit if new is not None else False})
    parser_changes = {field: {"old": before[field], "new": after[field]} for field in
                      ("parser", "parser_version", "policy_hash", "normalization_policy") if before[field] != after[field]}
    source_changed = before["artifact_sha"] != after["artifact_sha"]
    corpus_changed = previous["generation"] != current["generation"]
    content = {"schema_version": 1, "policy": "m3-bounded-exact-change-context-v1",
        "source": {"old_artifact_sha": before["artifact_sha"], "new_artifact_sha": after["artifact_sha"],
            "old_raw_sha": before["source"]["raw_sha"], "new_raw_sha": after["source"]["raw_sha"],
            "old_source_id": before["source"]["source_id"], "new_source_id": after["source"]["source_id"],
            "changed": source_changed, "parser_policy_changes": parser_changes,
            "ancestry": "same_immutable_artifact" if not source_changed else "unknown_requires_explicit_rebase",
            "unmatched_pages": {"old": list(range(len(after["pages"]), len(before["pages"]))),
                                "new": list(range(len(before["pages"]), len(after["pages"])))},
            "semantic_equivalence": "not_assessed"},
        "corpus": {"old_generation": previous["generation"], "new_generation": current["generation"],
            "changed": corpus_changed, "record_ids": record_ids,
            "policy_changed": previous["policy_hash"] != current["policy_hash"],
            "old_policy_hash": previous["policy_hash"], "new_policy_hash": current["policy_hash"],
            "old_complete": previous["complete"], "new_complete": current["complete"],
            "all_dependent_approvals_stale": corpus_changed},
        "all_dependent_approvals_stale": source_changed or corpus_changed,
        "counts": counts, "entries": entries, "truncated": bool(omitted), "omitted_entries": omitted,
        "display_limits": {"entries": limit, "quote_codepoints": quote_limit}}
    return {**content, "context_sha": digest("m3.change-context.v1", content)}
