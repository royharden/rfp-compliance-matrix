"""Original-coordinate structure and context without invented table geometry."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from rfp_matrix.contracts import M3Error, digest
from .artifacts import make_anchor, normalize_with_alignment, resolve_anchor, validate_artifact


def anchor_from_normalized(artifact: Mapping[str, Any], page_index: int,
                           start: int, end: int) -> dict[str, Any]:
    """Resolve exact normalized coordinates, never a first-substring match.

    A partial expansion (one s of original sharp-s, half a ligature, a reordered
    mark without its encompassing source) cannot become an exact-looking quote.
    """
    source = validate_artifact(artifact)
    if type(page_index) is not int or not 0 <= page_index < len(source["pages"]):
        raise M3Error("invalid_anchor", "Unknown page index")
    page = source["pages"][page_index]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(page["normalized_text"]):
        raise M3Error("invalid_normalized_anchor", "Normalized span exceeds page bounds")
    selected = page["alignment"][start:end]
    original_start = min(interval[0] for interval in selected)
    original_end = max(interval[1] for interval in selected)
    for index, (left, right) in enumerate(page["alignment"]):
        if not start <= index < end and left < original_end and right > original_start:
            raise M3Error("ambiguous_normalized_anchor", "Span cuts a Unicode expansion or reordered original interval")
    quote = page["text"][original_start:original_end]
    normalized, _ = normalize_with_alignment(quote, policy=source["normalization_policy"])
    if normalized != page["normalized_text"][start:end]:
        raise M3Error("ambiguous_normalized_anchor", "Selected normalized span does not roundtrip as one original span")
    section = next((section["section_id"] for section in page["sections"]
                    if section["start"] <= original_start < original_end <= section["end"]), None)
    return make_anchor(source, page_index, original_start, original_end, section_id=section)


def resolve_context(artifact: Mapping[str, Any], anchors: Sequence[Mapping[str, Any]],
                    *, radius: int = 180) -> dict[str, Any]:
    """Ordered multi-anchor context; cross-page clauses retain separate anchors.

    Adjacent page snippets are contextual only, never silently appended to quoted
    evidence. Duplicate quotes resolve at their own offsets. No source URL fetch.
    """
    source = validate_artifact(artifact, allow_historical=True)
    if type(radius) is not int or not 0 <= radius <= 2000 or not 1 <= len(anchors) <= 100:
        raise M3Error("context_limit", "Context needs 1..100 anchors and a bounded codepoint radius")
    resolved = []
    for anchor in anchors:
        literal = resolve_anchor(source, anchor)
        page_index = literal["page_index"]
        page = source["pages"][page_index]
        resolved.append({**literal,
                         "context": page["text"][max(0, literal["start"] - radius):literal["end"] + radius],
                         "previous_page_tail": source["pages"][page_index - 1]["text"][-radius:] if radius and page_index else "",
                         "next_page_head": source["pages"][page_index + 1]["text"][:radius] if radius and page_index + 1 < len(source["pages"]) else "",
                         "context_is_evidence": False, "coverage": page["coverage"], "issues": page["issues"]})
    return {"artifact_sha": source["artifact_sha"], "anchors": resolved,
            "cross_page": len({item["page_index"] for item in resolved}) > 1}


def structural_segments(artifact: Mapping[str, Any], *, max_segments: int = 10000) -> dict[str, Any]:
    """Traverse every original nonblank line and separately retain extracted cells.

    Lines are structural candidates, not an automatic atomic-requirement claim.
    Tables lack reliable text offsets from pdfplumber's cell strings; every cell
    remains explicitly unanchored and blocks complete structure traversal. A
    repeated cell string is never matched to its first occurrence in page text.
    """
    source = validate_artifact(artifact)
    if type(max_segments) is not int or not 1 <= max_segments <= 20000:
        raise M3Error("segment_limit", "Invalid structural segment bound")
    segments: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    unresolved: list[int] = []
    for page in source["pages"]:
        page_index = page["page_index"]
        if page["coverage"] != "ok" or page["issues"] or page["tables"]:
            unresolved.append(page_index)
        sections = page["sections"]
        for match in re.finditer(r"[^\n]+", page["text"]):
            left, right = match.start(), match.end()
            while left < right and page["text"][left].isspace():
                left += 1
            while right > left and page["text"][right - 1].isspace():
                right -= 1
            if left == right:
                continue
            if len(segments) + len(tables) >= max_segments:
                raise M3Error("segment_limit", "No partial structure result accepted; segment cap exceeded")
            literal = page["text"][left:right]
            section = next((item for item in sections if item["start"] <= left < right <= item["end"]), None)
            is_heading = section is not None and left == section["start"] and literal == section["title"]
            kind = "heading" if is_heading else ("bullet" if re.match(r"(?:[-*•]|\d+[.)])\s", literal) else "text")
            anchor = make_anchor(source, page_index, left, right, section_id=section["section_id"] if section else None)
            segments.append({"segment_id": digest("m3.structural-segment.v1", anchor),
                             "kind": kind, "anchor": anchor, "literal": literal,
                             "parent_section_id": section["section_id"] if section and not is_heading else None,
                             "uncertainty": sorted(set(page["issues"] + (["wrapped_line_atomicity_unknown"] if kind == "text" else [])))})
        for table_index, table in enumerate(page["tables"]):
            for row_index, row in enumerate(table):
                for column_index, cell in enumerate(row):
                    if cell is None or not cell.strip():
                        continue
                    if len(segments) + len(tables) >= max_segments:
                        raise M3Error("segment_limit", "No partial structure result accepted; cell cap exceeded")
                    tables.append({"page_index": page_index, "table_index": table_index,
                                   "row_index": row_index, "column_index": column_index,
                                   "literal": cell, "anchor": None, "uncertainty": ["table_cell_original_alignment_unavailable"]})
    return {"schema_version": 1, "artifact_sha": source["artifact_sha"], "segments": segments,
            "table_cells": tables, "complete": not unresolved, "unresolved_pages": unresolved,
            "policy": "m3-original-line-structure-v1"}
