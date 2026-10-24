"""Policy upgrade authority regression SOURCE ONLY; unrun in Dirty Mode."""
from copy import deepcopy
import csv
import io
import json

import pytest

from rfp_matrix.cli import build_parser, command_response
from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.export import export_matrix
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.review.application import ReviewerApplication
from rfp_matrix.review.graph import LangGraphReviewDriver
from rfp_matrix.source import SourceRegistry, parse_text_pages


@pytest.fixture
def policy_run(tmp_path):
    artifact = parse_text_pages(["The vendor must provide daily audit reports.\nFictional background describes a monthly worksheet."],
        source_id="synthetic-policy", terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    built = build_matrix(artifact, corpus)
    store = ReviewStore(tmp_path / "state")
    SourceRegistry(store.root / "sources").save(artifact)
    snapshot = store.create_run("R", artifact, corpus, built["rows"], inventory_report=built["inventory_report"])
    row = snapshot["rows"][0]
    arguments = dict(expected_assessment_generation=snapshot["assessment_generation"], expected_row_revision=row["decision_revision"],
                     request_id="old-gap", reason="Acknowledge absence in this fictional corpus only")
    event = store.decide("R", row["row_id"], "accept_gap", **arguments)
    return store, store.load_run("R"), event, arguments


def _unavailable(snapshot, status="mismatch"):
    failed = {"schema_version": 1, "status": status, "allows_current_claim": False, "execution_verified": False,
              "recorded_fingerprint": "1" * 64, "current_fingerprint": None, "reasons": ["Synthetic policy transition"],
              "action": "restart_then_reassess" if status == "restart_required" else "reimport_or_reassess"}
    return {**failed, "source": deepcopy(failed), "rows": {row["row_id"]: deepcopy(failed) for row in snapshot["rows"]}}


def test_captured_current_string_cannot_keep_old_approval_current_or_change_stored_cas(policy_run, monkeypatch):
    """what_bug_this_catches: trusting a naked current status/hash rather than evidence-layer validation of the supplied full actual descriptor."""
    import rfp_matrix.source as source
    store, before, _, _ = policy_run
    monkeypatch.setattr(source, "policy_context", lambda *args, **kwargs: {"status": "current", "fingerprint": "f" * 64})
    app = ReviewerApplication(store)
    view = app.get_run("R")
    assert view["policy_status"]["allows_current_claim"] is False
    assert view["rows"][0]["current_approval"] == "needs_reassessment"
    assert app.list_rows("R", "approved_gap")["rows"] == []
    assert len(app.list_rows("R", "stale")["rows"]) == len(app.list_rows("R", "unresolved")["rows"]) == 1
    assert store.load_run("R") == before  # Content/decision CAS and immutable decision are untouched by a read.
    context = app.source_context("R", before["rows"][0]["row_id"])
    assert context["literal_history_only"] is True and context["policy_status"]["allows_current_claim"] is False


@pytest.mark.parametrize("status", ["missing", "legacy", "mismatch", "restart_required"])
def test_policy_failure_blocks_fresh_gap_and_graph_recovery_but_exact_decision_retry_is_history(policy_run, monkeypatch, status):
    """what_bug_this_catches: an old current-approval intent being freshly resumed/approved after policy drift or missing provider semantics."""
    import rfp_matrix.review.currentness as currentness
    store, snapshot, event, original = policy_run
    monkeypatch.setattr(currentness, "policy_review", lambda value, **kwargs: _unavailable(value, status))
    row = snapshot["rows"][0]
    assert store.decide("R", row["row_id"], "accept_gap", **original) == event
    with pytest.raises(ReviewError) as error:
        store.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=snapshot["assessment_generation"],
            expected_row_revision=row["decision_revision"], request_id="fresh-gap", reason="Attempt after drift")
    assert error.value.code == "policy_not_current" and error.value.details["policy_status"]["status"] == status
    driver = LangGraphReviewDriver(store.root / "checkpoints.sqlite3")
    monkeypatch.setattr(driver, "_graph", lambda *args: (_ for _ in ()).throw(AssertionError("Checkpoint must not open")))
    with pytest.raises(ReviewError):
        driver.apply(store, "R", event)
    assert store.pending_intents("R") and store.load_run("R") == snapshot and store.events("R") == [event]


def test_named_reassessment_recomputes_human_pending_evidence_invalidates_approvals_and_retries(policy_run, monkeypatch):
    """what_bug_this_catches: hash-only refresh or no real assessment path for a promoted pending requirement, and approval carry on recomputation."""
    import rfp_matrix.evidence as evidence
    store, _, _, _ = policy_run
    wrapper = store.inventory_audit("R")
    candidate = next(entry for entry in wrapper["audit"]["entries"] if entry["promotable"])
    promoted = store.promote_inventory_candidate("R", {"candidate_id": candidate["candidate_id"], "expected_artifact_sha": wrapper["snapshot"]["artifact"]["artifact_sha"],
        "expected_assessment_generation": wrapper["assessment_generation"], "expected_matrix_generation": wrapper["matrix_generation"],
        "request_id": "human-add", "reason": "Possible omitted obligation, not approval"})
    before = store.load_run("R")
    real, calls = evidence.reassess_rows, []

    def recompute(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(evidence, "reassess_rows", recompute)
    arguments = dict(expected_matrix_generation=before["matrix_generation"], request_id="reassess1", reason="Explicit evidence refresh", method="B0a")
    after = store.reassess("R", **arguments)
    assert len(calls) == 1 and all(row["approval"] == "stale" for row in after["rows"])
    added = next(row for row in after["rows"] if row["row_id"] == promoted["row_id"])
    assert added["human_origin"] == promoted["row"]["human_origin"]
    assert added["assessment"]["policy_provenance"] and added["assessment"]["validation"]["method"] != "human_inventory_promotion"
    assert after["inventory_report"] == before["inventory_report"] and len(store.events("R")) == 1
    assert store.reassess("R", **arguments) == after and len(calls) == 1
    with pytest.raises(ReviewError) as error:
        store.reassess("R", **{**arguments, "request_id": "old-cas"})
    assert error.value.code == "stale_generation" and len(calls) == 1


@pytest.mark.parametrize("status", ["legacy", "mismatch", "restart_required"])
def test_parser_upgrade_or_process_drift_requires_reimport_or_restart_before_reassessment(policy_run, monkeypatch, status):
    """what_bug_this_catches: recomputing evidence as a fake repair for old parser coordinates, or executing in a drifted process."""
    import rfp_matrix.evidence as evidence
    import rfp_matrix.review.currentness as currentness
    store, snapshot, _, _ = policy_run
    monkeypatch.setattr(currentness, "policy_review", lambda value, **kwargs: _unavailable(value, status))
    monkeypatch.setattr(evidence, "reassess_rows", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("No producer before source/currentness guard")))
    with pytest.raises(ReviewError) as error:
        store.reassess("R", expected_matrix_generation=snapshot["matrix_generation"], request_id="upgrade", reason="Attempt policy repair")
    assert error.value.code == "policy_not_current" and store.load_run("R") == snapshot
    assert store.revision_request("R", "upgrade") is None


def test_upgrade_keeps_old_export_bytes_but_new_export_suppresses_current_gap_fields(policy_run, tmp_path, monkeypatch):
    """what_bug_this_catches: immutable export retries changing bytes or policy-invalid historical approvals leaking into fresh commitment columns."""
    import rfp_matrix.review.currentness as currentness
    store, snapshot, event, _ = policy_run
    output = tmp_path / "exports"
    arguments = dict(request_id="old-export", expected_matrix_generation=snapshot["matrix_generation"])
    receipt = export_matrix(store, "R", output, **arguments)
    directory = output / receipt["export_id"]
    old_bytes = {path.name: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setattr(currentness, "policy_review", lambda value, **kwargs: _unavailable(value))
    retry = export_matrix(store, "R", output, **arguments)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == old_bytes
    assert retry["current_at_return"] is False and retry["policy_current_at_return"] is False
    fresh = export_matrix(store, "R", output, request_id="new-policy-export", expected_matrix_generation=snapshot["matrix_generation"])
    new_dir = output / fresh["export_id"]
    rows = list(csv.DictReader(io.StringIO((new_dir / "matrix.csv").read_text(encoding="utf-8"))))
    assert rows[0]["positive_commitment"] == rows[0]["gap_acknowledgement"] == ""
    assert rows[0]["approval"] == "HISTORICAL_POLICY_APPROVED_GAP" and rows[0]["policy_status"] == "mismatch"
    manifest = json.loads((new_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["current_approval_event_ids"] == {} and manifest["approval_event_ids"] == {event["row_id"]: event["event_id"]}
    assert manifest["unresolved_inventory"][0]["row_id"] == event["row_id"]
    app = ReviewerApplication(store, export_root=output)
    with pytest.raises(ReviewError) as error:
        app.export_file("R", receipt["export_id"], "matrix.csv")
    assert error.value.code == "historical_export"
    assert app.download_export("R", "old-export", "matrix.csv")["current"] is False


def test_store_construction_does_not_collect_source_fingerprints_but_explicit_status_does(policy_run, monkeypatch):
    """what_bug_this_catches: constructor fingerprint I/O or absence being silently relabelled as verified baseline currentness."""
    import rfp_matrix.source as source
    store, snapshot, _, _ = policy_run
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(True)
        return {"status": "missing", "fingerprint": None}

    monkeypatch.setattr(source, "policy_context", unavailable)
    reopened = ReviewStore(store.root)
    assert calls == []
    result = reopened.policy_status("R")
    assert calls and result["policy_status"]["allows_current_claim"] is False
    assert result["runtime_verified"] is False and reopened.load_run("R") == snapshot


def test_final_cli_reassessment_flags_and_policy_error_preserve_machine_action_details():
    """what_bug_this_catches: missing named reassessment journey or JSON hiding the required restart/reimport action."""
    parser = build_parser()
    args = parser.parse_args(["reassess", "--run", "R", "--matrix-generation", "1" * 64,
        "--request-id", "Q1", "--reason", "Recompute", "--replay-tape", "fixture.m3-replay.json", "--provider-config", "policy.json"])
    assert args.method == "B0a" and args.allow_live is False
    enabled = parser.parse_args(["reassess", "--run", "R", "--matrix-generation", "1" * 64, "--request-id", "Q2", "--reason", "Future admission",
        "--method", "F1", "--provider-config", "policy.json", "--allow-live", "--run-envelope", "envelope.json", "--run-approval", "approval.json"])
    assert enabled.run_envelope == "envelope.json" and enabled.run_approval == "approval.json"
    assert parser.parse_args(["policy-status", "--run", "R"]).command == "policy-status"
    response = command_response(args, error=ReviewError("policy_not_current", "Restart required", policy_status={"status": "restart_required", "action": "restart_then_reassess"}))
    assert json.loads(json.dumps(response))["error"]["details"]["policy_status"]["action"] == "restart_then_reassess"
