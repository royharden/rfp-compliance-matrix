"""Unrun synthetic protocol cases; fakes below are not real custody or human gold."""
from dataclasses import replace
import json

import pytest

from rfp_matrix.eval.corpus import near_duplicate_report, validate_annotations
from rfp_matrix.eval.custody import VerifiedGrant, consume_seal_state
from rfp_matrix.eval.pipeline import RecordingPort
from rfp_matrix.eval.runner import (
    Authorization, PublicInput, PublicSpan, fingerprint, freeze_comparison,
    run_comparison, sha,
)


def setup_comparison():
    source = PublicSpan("S", "source", sha(b"artifact"), 0, 0, 4, "must")
    evidence = PublicSpan("E", "evidence", sha(b"vendor"), 0, 0, 3, "yes", "vendor-record")
    public = PublicInput("public-doc", "family-a", b"%PDF-synthetic-protocol-bytes",
                         b'{"fictional":true}', sha(b"rights-fixture"), "verified", (source, evidence))
    pins = {k: sha(k.encode()) for k in ("source", "parser", "normalizer", "questions", "thresholds", "split", "labels", "scorer", "budget", "price")}
    configs = {m: {"config_sha": sha(m.encode()), "model_snapshot": "fixture-exact",
                  "sdk_snapshot": "fixture-sdk-exact", "price_sha": pins["price"],
                  "budget_sha": pins["budget"], "origin": "baseline" if m == "B0a" else "mock"}
               for m in ("B0a", "B0b", "F1", "F2")}
    frozen = freeze_comparison((public,), split="dev", pins=pins, method_configs=configs,
                               dataset_counts={"documents": 1, "families": 1})
    return public, frozen


class CustodianFake:
    """In-memory fixture only; it authenticates no actual principal/independence."""

    def __init__(self, frozen, *, disclose=False):
        self.frozen, self.disclose, self.spent = frozen, disclose, False
        self.scored = []

    def authorize_and_consume(self, *, frozen_manifest, seal):
        if self.spent or seal != b"external-test-seal" or frozen_manifest != self.frozen.canonical_manifest:
            raise ValueError("Fixture denial")
        self.spent = True
        manifest = json.loads(frozen_manifest)
        return Authorization(self.frozen.digest, manifest["input_sha"], manifest["pins"]["split"],
            manifest["pins"]["labels"], sha(seal), sha(b"fixture-authorization"), self.disclose)

    def score(self, *, authorization, method, predictions):
        self.scored.append(method)
        return {"population_sha": fingerprint({"input_sha": authorization.input_sha,
                "labels_sha": authorization.labels_sha, "split_sha": authorization.split_sha}),
                "inventory_recall": {"numerator": 0, "denominator": 1},
                "failure_ids": ["case-a"], "mandatory_miss_ids": ["g-miss"]}


class MethodFake:
    def __init__(self, *, fail=False, atom_id="p"):
        self.seen, self.fail, self.atom_id = [], fail, atom_id

    def predict(self, *, method, document, config):
        self.seen.append((document, config))
        if self.fail:
            raise ValueError("PRIVATE_EXCEPTION_SENTINEL")
        return {"document_id": document.document_id, "atoms": [{"id": self.atom_id,
            "source_span_ids": ["S"], "mandatory": True, "conditional": False,
            "administrative": False, "uncertain": False}], "assessments": [{"atom_id": self.atom_id,
            "status": "supported", "retrieved_ids": ["vendor-record"], "support_ids": ["E"],
            "counterevidence_ids": []}], "usage": {"input_tokens": None, "output_tokens": None,
            "cost_usd": None, "latency_ms": None}}


def test_closed_public_inputs_no_gold_and_missing_arms_unmeasured():
    """what_bug_this_catches: seal/private label metadata leaks into method requests."""
    public, frozen = setup_comparison()
    port = MethodFake()
    receipt = run_comparison(frozen, (public,), ports={"B0a": port},
                             custodian=CustodianFake(frozen), seal=b"external-test-seal")
    document, config = port.seen[0]
    assert set(vars(document)) == {"document_id", "family_id", "pdf", "vendor_jsonl", "rights_record_sha", "rights_status", "spans"}
    assert set(config) == {"config_sha", "model_snapshot", "sdk_snapshot", "price_sha", "budget_sha", "origin"}
    assert receipt["methods"]["F1"]["status"] == "not_yet_measured"
    assert receipt["methods"]["B0a"]["usage"]["cost_usd"] is None
    assert receipt["methods"]["B0a"]["metrics"]["diagnostics"]["mandatory_miss_ids"] is None
    assert receipt["public_quality_claim"] is False


def test_exception_sanitized_and_failed_arm_unmeasured():
    """what_bug_this_catches: arbitrary exception secrets become public receipts/scores."""
    public, frozen = setup_comparison()
    receipt = run_comparison(frozen, (public,), ports={"F1": MethodFake(fail=True)},
                             custodian=CustodianFake(frozen), seal=b"external-test-seal")
    assert receipt["methods"]["F1"]["status"] == "failed_unmeasured"
    assert receipt["methods"]["F1"]["metrics"] is None
    assert "PRIVATE_EXCEPTION_SENTINEL" not in json.dumps(receipt)


def test_changed_raw_pin_and_wrong_seal_denied_before_method():
    """what_bug_this_catches: post-freeze input/pin mutation or foreign seal is admitted."""
    public, frozen = setup_comparison()
    port = MethodFake()
    with pytest.raises(ValueError):
        run_comparison(frozen, (replace(public, pdf=b"changed"),), ports={"B0a": port},
                       custodian=CustodianFake(frozen), seal=b"external-test-seal")
    with pytest.raises(ValueError):
        run_comparison(replace(frozen, digest=sha(b"other")), (public,), ports={"B0a": port},
                       custodian=CustodianFake(frozen), seal=b"external-test-seal")
    with pytest.raises(ValueError):
        run_comparison(frozen, (public,), ports={"B0a": port},
                       custodian=CustodianFake(frozen), seal=b"foreign-seal")
    assert port.seen == []


def test_spent_even_if_prediction_fails_and_disclosure_explicit():
    """what_bug_this_catches: failed runs recycle held-out input or silently disclose misses."""
    public, frozen = setup_comparison()
    custodian = CustodianFake(frozen, disclose=True)
    receipt = run_comparison(frozen, (public,), ports={"B0a": MethodFake(), "F1": MethodFake(fail=True)},
                             custodian=custodian, seal=b"external-test-seal")
    assert receipt["methods"]["B0a"]["metrics"]["diagnostics"]["mandatory_miss_ids"] == ["g-miss"]
    with pytest.raises(ValueError):
        run_comparison(frozen, (public,), ports={}, custodian=custodian, seal=b"external-test-seal")


def test_b0b_and_f1_must_share_inventory_before_scorer():
    """what_bug_this_catches: extraction drift is hidden in the matching-only ablation."""
    public, frozen = setup_comparison()
    custodian = CustodianFake(frozen)
    receipt = run_comparison(frozen, (public,), ports={"B0b": MethodFake(atom_id="p1"), "F1": MethodFake(atom_id="p2")},
                             custodian=custodian, seal=b"external-test-seal")
    assert receipt["matching_ablation"] == "inventory_mismatch"
    assert receipt["methods"]["F1"]["status"] == "failed_inventory_mismatch"
    assert custodian.scored == []


def test_recording_port_denies_extra_label_key_before_delegate():
    """what_bug_this_catches: an innocent-looking extra state field forwards gold to a model."""
    class Delegate:
        calls = 0
        def judge(self, state, questions):
            self.calls += 1
            return {}
    delegate = Delegate()
    with pytest.raises(ValueError):
        RecordingPort(delegate).judge({"clause": "public", "context": "public", "anchor": {}, "gold": "private"}, {})
    assert delegate.calls == 0


def test_related_family_mapping_blocks_split_leakage():
    """what_bug_this_catches: template/amendment near duplicates count as unseen families."""
    docs = [{"id": "a", "raw_sha": sha(b"a"), "text": "must provide daily backups",
             "issuer": "issuer-a", "template": "template-a", "declared_family": "fa", "split": "dev"},
            {"id": "b", "raw_sha": sha(b"b"), "text": "must provide daily backups",
             "issuer": "issuer-b", "template": "template-b", "declared_family": "fb", "split": "heldout"}]
    result = near_duplicate_report(docs)
    assert result["split_blocked"] is True
    assert {c["kind"] for c in result["conflicts"]} == {"family_mapping_conflict", "cross_split_related_documents"}


def test_pure_seal_transition_requires_role_and_never_unspends():
    """what_bug_this_catches: an author role or failed/reused seal can become eligible again."""
    fields = {name: sha(name.encode()) for name in ("principal_sha", "freeze_sha", "input_sha", "split_sha", "labels_sha", "family_set_sha", "seal_sha")}
    body = {"schema_version": 1, "status": "sealed", **fields}
    state = {**body, "revision": fingerprint(body)}
    grant = VerifiedGrant(**fields, role="independent_custodian", authorization_id_sha=sha(b"synthetic-grant"))
    with pytest.raises(ValueError):
        consume_seal_state(state, grant=replace(grant, role="implementing_author"), expected_revision=state["revision"])
    spent = consume_seal_state(state, grant=grant, expected_revision=state["revision"])
    assert spent["status"] == "spent"
    with pytest.raises(ValueError):
        consume_seal_state(spent, grant=grant, expected_revision=spent["revision"])


def test_leaf_counts_do_not_double_count_structural_parent():
    """what_bug_this_catches: gold parents plus children inflate acceptance denominators."""
    pages = {"page-1": "must"}
    anchor = {"page_id": "page-1", "start": 0, "end": 4, "quote": "must", "excerpt_sha": sha(b"must")}
    def label(key, parent, children):
        return {"id": key, "parent_id": parent, "child_ids": children, "anchors": [anchor],
                "mandatory": True, "conditional": False, "administrative": False,
                "condition": None, "applicability": "applies", "status": "cannot_answer_absent",
                "support_ids": [], "counterevidence_ids": [], "adjudicated": True}
    packet = {"schema_version": 1, "split": "dev", "document_id": "doc", "family_id": "family",
              "source_sha": sha(b"source"), "artifact_sha": sha(b"artifact"),
              "source_pages_sha": fingerprint(pages), "evidence_sha": fingerprint({}),
              "annotator": "synthetic-person-a", "adjudicator": "synthetic-person-b", "annotation_version": "v1",
              "atoms": [label("parent", None, ["a", "b"]), label("a", "parent", []), label("b", "parent", [])],
              "disagreements": []}
    summary = validate_annotations(packet, source_pages=pages, evidence={})
    assert summary["annotation_records"] == 3
    assert summary["atoms"] == summary["mandatory"] == 2
    assert summary["structural_parents"] == 1
    assert summary["human_identity_authenticated"] is False


def test_actual_registry_envelopes_are_consumed_as_anchors_and_pointers(monkeypatch):
    """what_bug_this_catches: adapter iterates producer envelope keys as if they were spans."""
    from rfp_matrix.eval.pipeline import prepare_public_input
    from rfp_matrix import source
    from rfp_matrix.evidence import engine
    raw, artifact_sha, corpus_sha = b"%PDF-envelope-fixture", sha(b"artifact"), sha(b"corpus")
    artifact = {"source": {"raw_sha": sha(raw), "source_id": "doc"}, "artifact_sha": artifact_sha}
    anchor = {"artifact_sha": artifact_sha, "page_index": 0, "start": 0, "end": 4, "quote": "must"}
    pointer = {"record_id": "record", "sentence_id": "record:0:3", "artifact_sha": sha(b"record"),
               "start": 0, "end": 3, "quote": "yes"}
    monkeypatch.setattr(source, "validate_artifact", lambda value: value)
    monkeypatch.setattr(source, "resolve_anchor", lambda artifact, anchor: anchor)
    monkeypatch.setattr(engine, "make_corpus", lambda records: {"generation": corpus_sha})
    monkeypatch.setattr(engine, "resolve_evidence", lambda corpus, pointer: pointer)
    monkeypatch.setattr(engine, "registered_source_candidates", lambda artifact: {
        "artifact_sha": artifact_sha, "classification_applied": False, "candidates": [{"anchor": anchor}]})
    monkeypatch.setattr(engine, "registered_evidence_windows", lambda corpus: {
        "corpus_generation": corpus_sha, "windows": [{"pointer": pointer}]})
    prepared = prepare_public_input(document_id="doc", family_id="family", pdf=raw,
        vendor_jsonl=b"{}", rights_record_sha=sha(b"rights-fixture"), terms="synthetic only",
        extractor=lambda raw, **kwargs: artifact)
    assert [span.kind for span in prepared.public.spans] == ["source", "evidence"]
    assert prepared.public.spans[1].record_id == "record"


def test_pipeline_factory_binds_extraction_artifact_not_original_hash():
    """what_bug_this_catches: admitted-source seam rejects an eval policy pinned only to raw bytes."""
    from rfp_matrix.eval.pipeline import PipelineMethodPort, prepare_public_input
    from rfp_matrix.eval.runner import canonical
    from rfp_matrix.providers import DisabledProvider
    from rfp_matrix.source import parse_text_pages
    pages = ["The supplier must provide daily encrypted backups."]
    raw = canonical(pages).encode("utf-8")
    artifact = parse_text_pages(pages, source_id="synthetic-doc", terms="authored development fixture")
    prepared = prepare_public_input(document_id="synthetic-doc", family_id="synthetic-family",
        pdf=raw, vendor_jsonl=b'{"record_id":"vendor","text":"No backups.","terms":"authored","fictional":true}',
        rights_record_sha=sha(b"synthetic-rights-reference"), terms="authored",
        extractor=lambda raw, **kwargs: artifact)
    policies = []
    def factory(config, **kwargs):
        if config.source_sha256 != artifact["artifact_sha"]:
            raise ValueError("consumer artifact mismatch")
        policies.append(config)
        return DisabledProvider("F1")
    port = PipelineMethodPort("B0a", (prepared,), factory=factory)
    config = port.frozen_configuration(price_sha=sha(b"price"), budget_sha=sha(b"budget"), origin="baseline")
    port.predict(method="B0a", document=prepared.public, config=config)
    assert len(policies) == 1
    assert policies[0].source_sha256 != sha(raw)
