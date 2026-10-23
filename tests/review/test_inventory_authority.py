"""Inventory authority/preflight regression SOURCE; never run in Dirty Mode."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from rfp_matrix.cli import build_parser, command_response
from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.export import export_matrix, verify_export
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.review.application import ReviewerApplication
from rfp_matrix.source import SourceRegistry, parse_text_pages


@pytest.fixture
def inventory(tmp_path):
    artifact = parse_text_pages([
        "The vendor must provide daily audit reports.\n"
        "Fictional background describes a monthly worksheet.\n"
        "A fictional introduction describes the reporting team."
    ], source_id="synthetic-inventory", terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    built = build_matrix(artifact, corpus)
    store = ReviewStore(tmp_path / "state")
    SourceRegistry(store.root / "sources").save(artifact)
    snapshot = store.create_run("R", artifact, corpus, built["rows"], inventory_report=built["inventory_report"])
    row = snapshot["rows"][0]
    store.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=snapshot["assessment_generation"],
        expected_row_revision=row["decision_revision"], request_id="gap-before-promotion", reason="Acknowledge only the missing fictional corpus support")
    wrapper = store.inventory_audit("R")
    candidates = [entry for entry in wrapper["audit"]["entries"] if entry["promotable"]]
    assert len(candidates) == 2
    return store, wrapper, candidates


def _payload(wrapper, candidate, request_id="promote1"):
    return {"candidate_id": candidate["candidate_id"], "expected_artifact_sha": wrapper["snapshot"]["artifact"]["artifact_sha"],
        "expected_assessment_generation": wrapper["assessment_generation"], "expected_matrix_generation": wrapper["matrix_generation"],
        "request_id": request_id, "reason": "Human correction of a possible omitted obligation; no approval"}


def test_promotion_restart_retry_preserves_machine_report_approvals_and_old_row_cas(inventory):
    """what_bug_this_catches: human additions overwriting machine predictions, reapproving rows or losing exact retry after restart."""
    store, wrapper, candidates = inventory
    before = store.load_run("R")
    decisions, intents = store.events("R"), store.pending_intents("R")
    payload = _payload(wrapper, candidates[0])
    first = store.promote_inventory_candidate("R", payload)
    after = store.load_run("R")
    assert after["rows"][:-1] == before["rows"]
    assert after["inventory_report"] == before["inventory_report"] and after["policy"] == before["policy"]
    assert after["assessment_generation"] == before["assessment_generation"]
    assert after["matrix_generation"] != before["matrix_generation"]
    row = after["rows"][-1]
    assert row["approval"] == "pending" and row["decision_event_id"] is None
    assert row["assessment"]["status"] == "needs_review" and row["assessment"]["support"] == []
    assert row["human_origin"]["actor"] == store.actor and row["human_origin"]["blind_method_prediction"] is False
    assert store.events("R") == decisions and store.pending_intents("R") == intents
    reopened = ReviewStore(store.root)
    retry = reopened.promote_inventory_candidate("R", payload)
    assert retry["replayed"] is True and retry["event"] == first["event"]
    assert len(reopened.inventory_audit("R")["promotions"]) == 1 and reopened.load_run("R") == after
    with pytest.raises(ReviewError) as error:
        reopened.promote_inventory_candidate("R", {**payload, "reason": "Changed intent with old key"})
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize("mutation", ["matrix", "assessment", "source", "unknown", "actor", "raw_span", "approval", "flag", "request_namespace"])
def test_stale_unregistered_forged_and_wrongly_typed_promotion_does_not_mutate(inventory, mutation):
    """what_bug_this_catches: stale/unknown source atoms or request-owned actor/approval/raw spans bypassing the trusted promotion port."""
    store, wrapper, candidates = inventory
    payload = _payload(wrapper, candidates[0])
    if mutation in {"matrix", "assessment", "source"}:
        key = {"matrix": "expected_matrix_generation", "assessment": "expected_assessment_generation", "source": "expected_artifact_sha"}[mutation]
        payload[key] = "0" * 64
    elif mutation == "unknown":
        payload["candidate_id"] = "R-unknown"
    elif mutation == "request_namespace":
        payload["request_id"] = "gap-before-promotion"
    else:
        payload.update({"actor": {"actor": "forged"}, "raw_span": {"start": 0}, "approval": {"approval": "approved_positive"},
                        "flag": {"mandatory": "false"}}[mutation])
    before = store.load_run("R")
    with pytest.raises(ValueError):
        store.promote_inventory_candidate("R", payload)
    assert store.load_run("R") == before and store.inventory_audit("R")["promotions"] == []


def test_duplicate_atom_and_total_row_bound_fail_without_second_event(inventory, monkeypatch):
    """what_bug_this_catches: fresh request keys duplicating a promoted atom or retired rows being omitted from the 2000-row cap."""
    store, wrapper, candidates = inventory
    store.promote_inventory_candidate("R", _payload(wrapper, candidates[0]))
    fresh = store.inventory_audit("R")
    with pytest.raises(ReviewError) as error:
        store.promote_inventory_candidate("R", _payload(fresh, candidates[0], "duplicate"))
    assert error.value.code == "duplicate_inventory_promotion"
    original = store._load

    def at_capacity(connection, run_id):
        snapshot = original(connection, run_id)
        snapshot["rows"] = [deepcopy(snapshot["rows"][0]) for _ in range(2000)]
        for row in snapshot["rows"]:
            row["retired"] = True
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(store, "_load", at_capacity)
        with pytest.raises(ReviewError) as error:
            store.promote_inventory_candidate("R", _payload(fresh, candidates[1], "over-cap"))
        assert error.value.code == "row_limit"
    assert len(store.inventory_audit("R")["promotions"]) == 1


def test_interrupted_promotion_rolls_back_row_history_and_placeholder_event(inventory, monkeypatch):
    """what_bug_this_catches: an interruption persisting a pending row without its final immutable human-origin event."""
    store, wrapper, candidates = inventory
    original = store.transaction

    class CrashConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE inventory_promotions SET event="):
                raise RuntimeError("Synthetic interruption before final promotion event commit")
            return self.connection.execute(sql, parameters)

    @contextmanager
    def interrupted(run_id):
        with original(run_id) as connection:
            yield CrashConnection(connection)

    before = store.load_run("R")
    with monkeypatch.context() as patch:
        patch.setattr(store, "transaction", interrupted)
        with pytest.raises(RuntimeError):
            store.promote_inventory_candidate("R", _payload(wrapper, candidates[0]))
    assert store.load_run("R") == before and store.inventory_audit("R")["promotions"] == []
    assert store.promote_inventory_candidate("R", _payload(wrapper, candidates[0]))["replayed"] is False


def test_export_freezes_original_audit_and_separate_unresolved_human_lineage(inventory, tmp_path):
    """what_bug_this_catches: exports hiding pending human rows or presenting their correction as a new blind machine prediction."""
    store, wrapper, candidates = inventory
    event = store.promote_inventory_candidate("R", _payload(wrapper, candidates[0]))["event"]
    current = store.load_run("R")
    receipt = export_matrix(store, "R", tmp_path / "exports", request_id="audit-export",
                            expected_matrix_generation=current["matrix_generation"])
    directory = tmp_path / "exports" / receipt["export_id"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    audit_file = json.loads((directory / "inventory-audit.json").read_text(encoding="utf-8"))
    assert manifest["snapshot"]["inventory_report"] == wrapper["snapshot"]["inventory_report"]
    assert audit_file["inventory_review"]["audit"] == wrapper["audit"]
    assert manifest["inventory_provenance"]["promotion_event_ids"] == [event["event_id"]]
    assert manifest["inventory_provenance"]["all_requirements_found"] is None
    assert event["row_id"] in {row["row_id"] for row in manifest["unresolved_inventory"]}
    assert event["row_id"] not in manifest["approval_event_ids"]
    assert verify_export(directory)["status"] == "structurally_verified"
    app = ReviewerApplication(store, export_root=tmp_path / "exports")
    download = app.download_export("R", "audit-export", "inventory-audit.json")
    assert download["content"] == (directory / "inventory-audit.json").read_bytes()
    assert download["current"] is False  # New pending human row has no current assessment until explicit reassess.
    assert download["policy_status"]["allows_current_claim"] is False


def test_preflight_uses_registered_counts_and_never_calls_factory_ledger_or_judge(inventory, monkeypatch):
    """what_bug_this_catches: diagnostic preview dispatching a provider or accepting caller-supplied counts instead of frozen registry counts."""
    import rfp_matrix.providers as providers
    from rfp_matrix.providers.live import OpenAIClassificationProvider, TypeSafeProvider
    store, wrapper, _ = inventory
    captured = {}

    def forbidden(*args, **kwargs):
        raise AssertionError("No provider/ledger dispatch belongs in preview")

    def planner(config, counts, **kwargs):
        captured.update(config=config, counts=counts, options=kwargs)
        return {"sentinel": "pure-plan", "remaining_budget": None}

    monkeypatch.setattr(providers, "estimate_run_envelope", planner)
    monkeypatch.setattr(providers, "create_provider", forbidden)
    monkeypatch.setattr(providers, "CallLedger", forbidden)
    monkeypatch.setattr(TypeSafeProvider, "judge", forbidden)
    monkeypatch.setattr(OpenAIClassificationProvider, "judge", forbidden)
    app = ReviewerApplication(store)
    before = store.load_run("R")
    result = app.run_envelope("R", {})
    assert captured["config"].enabled is False and captured["options"]["method"] == "B0a"
    assert captured["counts"]["inventory_candidates"] == len(wrapper["audit"]["entries"])
    assert captured["counts"]["requirement_rows"] == len(before["rows"])
    assert captured["counts"]["replay_covered_requests"] is None
    assert captured["counts"]["retrieval_top_k"] == captured["counts"]["retrieved_evidence_candidates"] == 0
    assert result["envelope"] == {"sentinel": "pure-plan", "remaining_budget": None}
    assert result["dispatch_performed"] is result["key_checked"] is result["ledger_read"] is False
    assert store.load_run("R") == before
    for invalid in ({"counts": {}}, {"method": "F1"}, {"config": {"source_sha256": "0" * 64}}):
        with pytest.raises(ValueError):
            app.run_envelope("R", invalid)


def test_actual_cli_flags_and_machine_conflict_versions_are_explicit():
    """what_bug_this_catches: documented CLI flags drifting or stale promotion errors losing exact refresh identities in JSON."""
    parser = build_parser()
    args = parser.parse_args(["promote-inventory", "--run", "R", "--candidate-id", "R-a", "--artifact-sha", "1" * 64,
        "--assessment-generation", "2" * 64, "--matrix-generation", "3" * 64, "--request-id", "P1", "--reason", "Review",
        "--mandatory", "false", "--conditional", "true", "--applicability", "unknown"])
    assert args.mandatory == "false" and args.conditional == "true"
    wire = command_response(args, error=ReviewError("stale_promotion", "Refresh", matrix_generation="4" * 64))
    assert wire["status"] == "conflict" and wire["versions"]["expected"]["matrix_generation"] == "3" * 64
    assert wire["versions"]["current"]["matrix_generation"] == "4" * 64
    assert json.loads(json.dumps(wire))["error"]["code"] == "stale_promotion"
    assert parser.parse_args(["run-envelope", "--run", "R"]).__dict__["method"] == "B0a"
