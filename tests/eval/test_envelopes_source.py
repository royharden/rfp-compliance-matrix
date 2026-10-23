"""UNRUN synthetic prospective-envelope contracts; no model/corpus/custody evidence."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from rfp_matrix.eval.envelopes import pipeline_envelope_commitments
from rfp_matrix.eval.runner import Authorization, PublicInput, PublicSpan, freeze_comparison, run_comparison, sha
from rfp_matrix.providers import ProviderConfig


def setup_context(*, known_price=False, windows=1):
    public = PublicInput("doc", "family", b"synthetic-protocol-bytes", b'{"fictional":true}',
        sha(b"synthetic-rights"), "verified", (PublicSpan("S", "source", "a" * 64, 0, 0, 4, "must"),))
    pins = {key: sha(key.encode()) for key in ("source", "parser", "normalizer", "questions", "thresholds", "split", "labels", "scorer", "budget", "price")}
    methods, ports, counts = {}, {}, {}
    for method in ("B0a", "B0b", "F1", "F2"):
        mapping = {"method": "F1" if method == "B0a" else method, "document_id": "doc",
                   "source_sha256": "a" * 64, "corpus_sha256": "b" * 64,
                   "max_input_tokens": 8000, "max_output_tokens": 1024, "deadline_seconds": 60.0}
        if method == "F2":
            mapping.update(model="gpt-fixture-2026-01-01", sdk_name="python-stdlib-urllib", sdk_version="fixture-only")
        if known_price:
            mapping.update(input_per_million_usd="1", output_per_million_usd="2",
                           price_policy="fixture-price", price_provenance="authored-price-not-live")
        policy = ProviderConfig.from_mapping(mapping)
        config = {"config_sha": sha(method.encode()), "model_snapshot": policy.model,
                  "sdk_snapshot": f"{policy.sdk_name}=={policy.sdk_version}", "origin": "baseline" if method == "B0a" else "mock",
                  "price_sha": pins["price"], "budget_sha": pins["budget"]}
        methods[method] = config
        # An in-memory port stub supplies no predictions and creates no client.
        class Port:
            def __init__(self, policy, config):
                self.policy, self.config = policy, config
                self.prepared = {"doc": SimpleNamespace(public=public)}
            def prospective_policy(self, document_id):
                return self.policy
            def frozen_configuration(self, **kwargs):
                return self.config
        ports[method] = Port(policy, config)
        counts[method] = {"doc": {"schema_version": 1, "artifact_sha": "a" * 64,
            "corpus_generation": "b" * 64, "registry_sha256": "c" * 64,
            "assessment_generation": "d" * 64, "matrix_generation": "e" * 64,
            "inventory_candidates": 1, "requirement_rows": 1, "retrieval_top_k": 1,
            "max_windows_per_candidate": windows}}
    return public, pins, methods, ports, counts


def freeze(public, pins, methods, envelopes):
    return freeze_comparison((public,), split="dev", pins=pins, method_configs=methods,
        dataset_counts={"documents": 1, "families": 1}, run_envelopes=envelopes)


def test_native_fanout_unknown_price_and_disabled_are_distinct():
    """what_bug_this_catches: question/window counts multiply calls or unknown price becomes free."""
    public, pins, methods, ports, counts = setup_context()
    envelopes = pipeline_envelope_commitments(ports, counts, methods)
    item = envelopes["F1"]["documents"][0]
    assert item["max_requests"] == 2  # one inventory bundle + one evidence bundle
    assert item["max_transport_attempts"] == 4
    assert item["max_input_tokens"] == 32000
    assert item["max_output_tokens"] == 4096
    assert item["max_concurrent_calls"] == 2
    assert item["status"] == "unknown_price" and item["worst_case_cost_usd"] is None
    assert item["dispatch_enabled"] is False
    assert envelopes["F1"]["measured"] is False
    assert envelopes["F1"]["status"] == "partial_unknown"
    assert envelopes["B0b"]["documents"][0]["max_requests"] == 1
    assert envelopes["B0a"]["documents"][0]["max_requests"] == 0
    freeze(public, pins, methods, envelopes)


def test_mock_incremental_zero_does_not_replace_declared_live_bound():
    """what_bug_this_catches: simulated/replayed incremental zero is reported as a live cost bound."""
    public, pins, methods, ports, counts = setup_context(known_price=True)
    envelopes = pipeline_envelope_commitments(ports, counts, methods)
    item = envelopes["F1"]["documents"][0]
    assert item["worst_case_cost_usd"] == "0.040192"  # hand arithmetic: (32000 + 2*4096)/1e6
    assert item["whole_run_affordable"] is None  # no declared remaining-ledger snapshot
    assert item["remaining_budget_basis"] == "unknown_no_ledger_snapshot"
    assert item["status"] == "partial_unknown"
    assert item["dispatch_enabled"] is False and envelopes["F1"]["measured"] is False
    freeze(public, pins, methods, envelopes)


def test_missing_context_and_truncated_windows_remain_partial():
    """what_bug_this_catches: absent generation context or clipped choice coverage looks complete."""
    public, pins, methods, ports, counts = setup_context(windows=300)
    counts["F2"] = {}
    envelopes = pipeline_envelope_commitments(ports, counts, methods)
    assert envelopes["F2"]["status"] == "not_yet_estimated"
    assert envelopes["F1"]["status"] == "partial_unknown"
    assert "evidence_choice_windows_truncated" in envelopes["F1"]["documents"][0]["conditions"]
    assert envelopes["F1"]["documents"][0]["max_requests"] == 2
    freeze(public, pins, methods, envelopes)


def test_changed_envelope_rejected_by_old_seal_before_prediction():
    """what_bug_this_catches: preflight/request bound changes but an old prospective seal still dispatches."""
    public, pins, methods, ports, counts = setup_context()
    envelopes = pipeline_envelope_commitments(ports, counts, methods)
    original = freeze(public, pins, methods, envelopes)
    changed = deepcopy(envelopes)
    changed["F1"]["documents"][0]["max_requests"] += 1
    revised = freeze(public, pins, methods, changed)
    old_manifest = json.loads(original.canonical_manifest)
    class OldSealFake:
        def authorize_and_consume(self, **kwargs):
            return Authorization(original.digest, old_manifest["input_sha"], pins["split"], pins["labels"],
                                 sha(b"fixture-seal"), sha(b"synthetic-authorization"))
    class NoPrediction:
        def predict(self, **kwargs):
            raise AssertionError("changed seal reached prediction")
    with pytest.raises(ValueError):
        run_comparison(revised, (public,), ports={"F1": NoPrediction()}, custodian=OldSealFake(), seal=b"fixture-seal")


def test_changed_budget_raw_input_and_extra_metadata_rejected():
    """what_bug_this_catches: unrelated budget/input or secret/gold metadata enters public envelope receipts."""
    public, pins, methods, ports, counts = setup_context()
    envelopes = pipeline_envelope_commitments(ports, counts, methods)
    for change in ("budget", "raw", "extra"):
        value = deepcopy(envelopes)
        if change == "budget":
            value["F1"]["budget_policy_sha"] = sha(b"other-budget")
        elif change == "raw":
            value["F1"]["documents"][0]["raw_sha"] = sha(b"other-input")
        else:
            value["F1"]["documents"][0]["gold_labels"] = []
        with pytest.raises(ValueError):
            freeze(public, pins, methods, value)


def test_blind_bound_cannot_use_corrected_lower_row_count_or_unknown_keys():
    """what_bug_this_catches: correction/gold count understates prospective work or leaks into estimator."""
    _, _, methods, ports, counts = setup_context()
    counts["F1"]["doc"]["requirement_rows"] = 0
    with pytest.raises(ValueError):
        pipeline_envelope_commitments(ports, counts, methods)
    counts["F1"]["doc"]["requirement_rows"] = 1
    counts["F1"]["doc"]["gold_labels"] = []
    with pytest.raises(ValueError):
        pipeline_envelope_commitments(ports, counts, methods)


def test_remaining_snapshot_is_explicit_and_changes_frozen_authorization():
    """what_bug_this_catches: missing balance becomes fresh budget or snapshot changes reuse seal."""
    public, pins, methods, ports, counts = setup_context(known_price=True)
    absent = pipeline_envelope_commitments(ports, counts, methods)
    balances = {"F1": {"doc": {"schema_version": 1, "document_id": "doc", "origin": "mock",
        "batch_committed_usd": "0", "document_committed_usd": "0", "ledger_snapshot_sha256": "f" * 64}}}
    supplied = pipeline_envelope_commitments(ports, counts, methods, budget_contexts=balances)
    old_item = absent["F1"]["documents"][0]
    new_item = supplied["F1"]["documents"][0]
    assert old_item["whole_run_affordable"] is None and old_item["budget_snapshot_sha"] is None
    assert new_item["whole_run_affordable"] is False  # explicit zero remaining cap, not a live grant
    assert new_item["remaining_budget_basis"] == "caller_supplied_ledger_snapshot"
    assert new_item["budget_snapshot_sha"] == "f" * 64
    assert freeze(public, pins, methods, absent) != freeze(public, pins, methods, supplied)
    changed = deepcopy(balances)
    changed["F1"]["doc"]["ledger_snapshot_sha256"] = "e" * 64
    revised = pipeline_envelope_commitments(ports, counts, methods, budget_contexts=changed)
    assert freeze(public, pins, methods, revised) != freeze(public, pins, methods, supplied)
