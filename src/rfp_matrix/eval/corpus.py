"""Pure corpus intake/annotation validation; no downloads, labels or custody I/O.

Run held-out annotation functions only in the separately operated custodian
boundary. This module grants no rights and authenticates no human identity.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .runner import fingerprint, require_digest, sha


class RightsAuthority(Protocol):
    """Host resolves identity/permission and verifies the cited rights evidence."""

    def authorize(self, *, actor: str, document_id: str, evidence_sha: str,
                  evidence_version: str, intended_use: str) -> bool: ...


def _text(value: Any, name: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Expected bounded nonempty {name}")
    return value


def rights_revision(*, raw: bytes, document_id: str, source_url: str,
                    source_version: str, retrieved_at: str, terms: str,
                    status: str = "pending", evidence_sha: str | None = None,
                    evidence_version: str | None = None, actor: str,
                    reason: str, intended_use: str,
                    previous: Mapping[str, Any] | None = None,
                    expected_revision: str | None = None,
                    authority: RightsAuthority | None = None) -> dict[str, Any]:
    """Prepare an append-only rights revision, never mutate/approve an old record.

    The persistence owner must atomically CAS expected_revision, preserve every
    event, and bind actor to its authenticated application principal. A return
    value is a proposed record, not durable publication permission.
    """
    for name, value in (("document_id", document_id), ("source_url", source_url),
                        ("source_version", source_version), ("retrieved_at", retrieved_at),
                        ("terms", terms), ("actor", actor), ("reason", reason),
                        ("intended_use", intended_use)):
        _text(value, name)
    if not isinstance(raw, bytes) or not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("Intake requires bounded original bytes")
    if status not in ("pending", "verified", "excluded"):
        raise ValueError("Unknown rights status")
    prior_revision = None
    if previous is not None:
        previous = dict(previous)
        prior_revision = previous.get("revision")
        body = {k: v for k, v in previous.items() if k != "revision"}
        if fingerprint(body) != prior_revision or previous.get("document_id") != document_id:
            raise ValueError("Invalid previous rights lineage")
        if previous.get("raw_sha") != sha(raw):
            raise ValueError("Changed raw document requires a new document identity")
    if expected_revision != prior_revision:
        raise ValueError("Stale rights revision")
    if evidence_sha is not None:
        require_digest(evidence_sha)
        _text(evidence_version, "evidence_version")
    elif evidence_version is not None:
        raise ValueError("Evidence version without evidence hash")
    if status == "verified":
        if (evidence_sha is None or authority is None
                or authority.authorize(actor=actor, document_id=document_id,
                    evidence_sha=evidence_sha, evidence_version=evidence_version,
                    intended_use=intended_use) is not True):
            raise ValueError("Verified rights require external authorization and versioned evidence")
    record = {"schema_version": 1, "document_id": document_id, "raw_sha": sha(raw),
              "source_url": source_url, "source_version": source_version,
              "retrieved_at": retrieved_at, "terms": terms, "status": status,
              "evidence_sha": evidence_sha, "evidence_version": evidence_version,
              "actor": actor, "reason": reason, "intended_use": intended_use,
              "previous_revision": prior_revision}
    return {**record, "revision": fingerprint(record)}


def validate_pack(records: Sequence[Mapping[str, Any]], *, intended_use: str) -> dict[str, Any]:
    """Reject pending/excluded/stale rights snapshots; do not infer public license."""
    ids, hashes = set(), set()
    for record in records:
        if fingerprint({k: v for k, v in record.items() if k != "revision"}) != record.get("revision"):
            raise ValueError("Changed rights record")
        if record.get("status") != "verified" or record.get("intended_use") != intended_use:
            raise ValueError("Pack contains unverified rights or wrong use permission")
        require_digest(record["raw_sha"])
        require_digest(record["evidence_sha"])
        _text(record.get("evidence_version"), "evidence_version")
        key = record["document_id"]
        if key in ids or record["raw_sha"] in hashes:
            raise ValueError("Duplicate document or raw bytes in pack")
        ids.add(key)
        hashes.add(record["raw_sha"])
    if not records:
        raise ValueError("Pack is empty")
    return {"document_count": len(records), "rights_snapshot_sha": fingerprint(list(records)),
            "status": "validated_declared_records", "permission_authenticated_here": False}


def near_duplicate_report(documents: Sequence[Mapping[str, Any]], *, threshold: float = 0.8) -> dict[str, Any]:
    """Bounded exact-token Jaccard screening, never proof of unrelated families.

    Each public extracted document supplies id, raw_sha, text, issuer, template,
    declared_family, split. Issuer/template equality and exact byte duplicates
    also connect families, even when token overlap is small. Human review of
    low-similarity documents and cross-format amendments is still required.
    """
    if type(threshold) not in (int, float) or not 0 < threshold <= 1 or not 1 <= len(documents) <= 100:
        raise ValueError("Expected bounded documents and 0<threshold<=1")
    indexed = {}
    tokens = {}
    for document in documents:
        if set(document) != {"id", "raw_sha", "text", "issuer", "template", "declared_family", "split"}:
            raise ValueError("Closed public duplicate-check record required")
        key = _text(document["id"], "document ID", 160)
        if key in indexed or document["split"] not in ("dev", "heldout"):
            raise ValueError("Duplicate ID or invalid split")
        for name in ("issuer", "template", "declared_family"):
            _text(document[name], name, 160)
        require_digest(document["raw_sha"])
        text = _text(document["text"], "public extracted text", 2_000_000)
        indexed[key] = document
        tokens[key] = set(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))
    parent = {key: key for key in indexed}

    def root(key: str) -> str:
        while parent[key] != key:
            key = parent[key]
        return key

    edges = []
    ids = sorted(indexed)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            a, b = indexed[left], indexed[right]
            union = tokens[left] | tokens[right]
            score = len(tokens[left] & tokens[right]) / len(union) if union else 0.0
            reasons = [field for field in ("raw_sha", "issuer", "template", "declared_family") if a[field] == b[field]]
            if score >= threshold:
                reasons.append("token_jaccard")
            if reasons:
                parent[root(right)] = root(left)
                edges.append({"left": left, "right": right, "reasons": reasons, "jaccard": score})
    groups: dict[str, list[str]] = {}
    for key in ids:
        groups.setdefault(root(key), []).append(key)
    conflicts = []
    for members in groups.values():
        if len({indexed[k]["declared_family"] for k in members}) > 1:
            conflicts.append({"kind": "family_mapping_conflict", "document_ids": members})
        if len({indexed[k]["split"] for k in members}) > 1:
            conflicts.append({"kind": "cross_split_related_documents", "document_ids": members})
    return {"schema_version": 1, "threshold": threshold, "policy": "nfkc-wordset-jaccard-issuer-template-v1",
            "input_sha": fingerprint(list(documents)), "edges": edges,
            "connected_groups": list(groups.values()), "conflicts": conflicts,
            "split_blocked": bool(conflicts), "human_family_review_required": True,
            "independent_custody_verified": False}


def validate_annotations(packet: Mapping[str, Any], *,
                         source_pages: Mapping[str, str],
                         evidence: Mapping[str, str]) -> dict[str, Any]:
    """Validate explicit human labels only in the authorized annotation boundary.

    Source pages use opaque stable IDs. Python string offsets address originals;
    evidence IDs refer to registered sentences/windows. This checks shape/literal
    consistency, not human expertise, independence or semantic truth.
    """
    fields = {"schema_version", "split", "document_id", "family_id", "source_sha", "artifact_sha", "source_pages_sha", "evidence_sha",
              "annotator", "adjudicator", "annotation_version", "atoms", "disagreements"}
    if set(packet) != fields or type(packet["schema_version"]) is not int or packet["schema_version"] != 1 or packet["split"] not in ("dev", "heldout"):
        raise ValueError("Invalid annotation envelope")
    for key in ("document_id", "family_id", "annotator", "annotation_version"):
        _text(packet[key], key, 160)
    for key in ("source_sha", "artifact_sha", "source_pages_sha", "evidence_sha"):
        require_digest(packet[key])
    if packet["source_pages_sha"] != fingerprint(dict(source_pages)) or packet["evidence_sha"] != fingerprint(dict(evidence)):
        raise ValueError("Annotation page/evidence mapping changed from committed snapshot")
    if packet["adjudicator"] is not None:
        _text(packet["adjudicator"], "adjudicator", 160)
        if packet["adjudicator"] == packet["annotator"]:
            raise ValueError("Adjudicator must be distinct from annotator")
    atoms = packet["atoms"]
    if not isinstance(atoms, list) or not atoms or len(atoms) > 2000:
        raise ValueError("Expected bounded nonempty atom labels")
    indexed = {}
    for atom in atoms:
        if set(atom) != {"id", "parent_id", "child_ids", "anchors", "mandatory", "conditional", "administrative", "condition", "applicability", "status", "support_ids", "counterevidence_ids", "adjudicated"}:
            raise ValueError("Invalid closed atom label")
        key = _text(atom["id"], "atom ID", 160)
        if key in indexed:
            raise ValueError("Duplicate human atom ID")
        indexed[key] = atom
        if any(type(atom[k]) is not bool for k in ("mandatory", "conditional", "administrative", "adjudicated")):
            raise ValueError("Explicit label booleans required")
        if atom["conditional"]:
            _text(atom["condition"], "condition")
        elif atom["condition"] is not None:
            raise ValueError("Unconditional atom cannot carry hidden condition")
        if atom["applicability"] not in ("applies", "unknown", "not_applies") or atom["status"] not in ("supported", "partial", "cannot_answer_absent", "cannot_answer_conflicting"):
            raise ValueError("Invalid applicability or evidence status")
        if not isinstance(atom["anchors"], list) or not 1 <= len(atom["anchors"]) <= 100:
            raise ValueError("Source-bound anchors required")
        for anchor in atom["anchors"]:
            if set(anchor) != {"page_id", "start", "end", "quote", "excerpt_sha"}:
                raise ValueError("Invalid literal anchor")
            text = source_pages.get(anchor["page_id"])
            start, end = anchor["start"], anchor["end"]
            if (text is None or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text) or text[start:end] != anchor["quote"]
                    or sha(text[start:end].encode("utf-8")) != anchor["excerpt_sha"]):
                raise ValueError("Human label does not resolve to exact original string")
        for field in ("support_ids", "counterevidence_ids"):
            refs = atom[field]
            if not isinstance(refs, list) or len(refs) > 200 or any(not isinstance(r, str) or r not in evidence for r in refs) or len(refs) != len(set(refs)):
                raise ValueError("Unknown/duplicate human evidence reference")
        if atom["status"] == "supported" and (not atom["support_ids"] or atom["counterevidence_ids"]):
            raise ValueError("Supported gold requires positive evidence without contradiction")
        if atom["status"] == "cannot_answer_conflicting" and not atom["counterevidence_ids"]:
            raise ValueError("Conflict label needs explicit counterevidence")
        if atom["status"] == "cannot_answer_absent" and (atom["support_ids"] or atom["counterevidence_ids"]):
            raise ValueError("Absent label cannot carry positive/conflicting evidence")
    for key, atom in indexed.items():
        children = atom["child_ids"]
        if not isinstance(children, list) or any(not isinstance(c, str) or c not in indexed for c in children) or len(children) != len(set(children)):
            raise ValueError("Invalid child lineage")
        parent_id = atom["parent_id"]
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in indexed or key not in indexed[parent_id]["child_ids"]):
            raise ValueError("Missing reciprocal parent lineage")
        if any(indexed[child]["parent_id"] != key for child in children):
            raise ValueError("Missing reciprocal child lineage")
        seen = {key}
        while parent_id is not None:
            if parent_id in seen:
                raise ValueError("Cyclic atomic lineage")
            seen.add(parent_id)
            parent_id = indexed[parent_id]["parent_id"]
    disagreements = packet["disagreements"]
    if not isinstance(disagreements, list) or len(disagreements) > 10000:
        raise ValueError("Expected bounded disagreement ledger")
    unresolved = []
    for event in disagreements:
        if set(event) != {"atom_id", "field", "first_label_sha", "second_label_sha", "resolution_sha", "reason"} or event["atom_id"] not in indexed:
            raise ValueError("Invalid disagreement record")
        if event["field"] not in ("atomicity", "mandatory", "conditional", "administrative", "applicability", "evidence"):
            raise ValueError("Unknown disagreement dimension")
        for key in ("first_label_sha", "second_label_sha"):
            require_digest(event[key])
        if event["resolution_sha"] is None:
            unresolved.append(event["atom_id"])
        else:
            require_digest(event["resolution_sha"])
            _text(event["reason"], "adjudication rationale")
            if packet["adjudicator"] is None:
                raise ValueError("Resolution requires named adjudicator")
    needs_second = [a["id"] for a in atoms if a["mandatory"] or a["status"] == "cannot_answer_conflicting"]
    missing_second = [key for key in needs_second if not indexed[key]["adjudicated"]]
    eligible = not unresolved and not missing_second and packet["adjudicator"] is not None
    leaves = [atom for atom in atoms if not atom["child_ids"]]
    return {"annotation_sha": fingerprint(packet), "status": "structure_valid_only",
            "annotation_records": len(atoms), "structural_parents": len(atoms) - len(leaves),
            "atoms": len(leaves), "mandatory": sum(a["mandatory"] for a in leaves),
            "absent": sum(a["status"] == "cannot_answer_absent" for a in leaves),
            "conflicting": sum(a["status"] == "cannot_answer_conflicting" for a in leaves),
            "conditional": sum(a["conditional"] for a in leaves),
            "administrative": sum(a["administrative"] for a in leaves),
            "conditional_admin_union": sum(a["conditional"] or a["administrative"] for a in leaves),
            "unresolved_disagreement_count": len(unresolved), "missing_second_review_count": len(missing_second),
            "declared_adjudication_complete": eligible, "human_identity_authenticated": False,
            "quality_acceptance": False}
