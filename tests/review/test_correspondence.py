"""Amendment authority regression SOURCE ONLY; unrun during Dirty Mode."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.review.operations import diagnostics, restore_store
from rfp_matrix.source import SourceRegistry, parse_text_pages


@pytest.fixture
def comparison(tmp_path):
    store = ReviewStore(tmp_path / "state")
    corpus = make_corpus([])
    old_artifact = parse_text_pages(["The vendor must provide daily audit reports.\nThe vendor must retain audit records for 30 days."],
        source_id="synthetic-amendment", terms="Self-authored fictional development fixture")
    registry = SourceRegistry(store.root / "sources")
    registry.save(old_artifact)
    old = store.create_run("R", old_artifact, corpus, build_matrix(old_artifact, corpus)["rows"])
    row = old["rows"][0]
    store.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=old["assessment_generation"],
                 expected_row_revision=row["decision_revision"], request_id="old-gap", reason="Acknowledge only this corpus absence")
    old = store.load_run("R")
    artifact = parse_text_pages(["The vendor must provide weekly audit reports.\nThe vendor must retain audit records for 60 days."],
        source_id="synthetic-amendment", terms="Self-authored fictional development fixture")
    registry.save(artifact)
    store.update_run("R", artifact, corpus, build_matrix(artifact, corpus)["rows"],
                     expected_matrix_generation=old["matrix_generation"], reason="rfp_amendment")
    wrapper = store.amendments("R")
    assert wrapper["comparison"]["candidates"]  # Fixture has lexical proposals, not semantic labels.
    return store, wrapper


def _payload(wrapper, *, request_id="A1", relation="one_to_one", old_count=1, new_count=1):
    report = wrapper["comparison"]
    old = [row for row in report["old_rows"] if not row["retired"]][:old_count]
    new = [row for row in report["new_rows"] if not row["retired"]][:new_count]
    return {"relation": relation, "old_rows": [{"row_id": r["row_id"], "row_revision": r["row_revision"]} for r in old],
        "new_rows": [{"row_id": r["row_id"], "row_revision": r["row_revision"]} for r in new],
        "expected_old_matrix_generation": report["old_identity"]["matrix_generation"],
        "expected_matrix_generation": report["new_identity"]["matrix_generation"],
        "expected_old_assessment_generation": report["old_identity"]["assessment_generation"],
        "expected_new_assessment_generation": report["new_identity"]["assessment_generation"],
        "expected_old_artifact_sha": report["old_identity"]["artifact_sha"], "expected_new_artifact_sha": report["new_identity"]["artifact_sha"],
        "expected_correspondence_revision": wrapper["correspondence_revision"], "request_id": request_id,
        "reason": "Human correspondence assertion only; no compliance or approval claim"}


def test_mapping_restart_exact_retry_and_no_approval_or_graph_intent_carry(comparison):
    """what_bug_this_catches: mapping history lost on restart or a lineage confirmation silently reapproving/creating resume intent."""
    store, wrapper = comparison
    before, decisions, pending = store.load_run("R"), store.events("R"), store.pending_intents("R")
    assert all(row["approval"] == "stale" for row in before["rows"])
    payload = _payload(wrapper)
    first = store.record_correspondence("R", payload)
    assert store.load_run("R") == before and store.events("R") == decisions and store.pending_intents("R") == pending
    assert first["approval_carry_allowed"] is False and first["decision_changed"] is False
    reopened = ReviewStore(store.root)
    repeated = reopened.record_correspondence("R", payload)
    assert repeated["event"] == first["event"] and repeated["replayed"] is True
    current = reopened.amendments("R")
    assert current["human_events"] == [first["event"]]
    assert current["correspondence_revision"] == first["event"]["revision"]
    assert reopened.correspondence_history("R")["total_events"] == 1
    with pytest.raises(ReviewError) as error:
        reopened.record_correspondence("R", {**payload, "reason": "Changed same-key intent"})
    assert error.value.code == "idempotency_conflict"


def test_mapping_ledger_cas_is_independent_from_matrix_and_reports_refresh_revision(comparison):
    """what_bug_this_catches: concurrent confirmations overwriting lineage because matrix generation did not change."""
    store, wrapper = comparison
    payload = _payload(wrapper)
    first = store.record_correspondence("R", payload)
    with pytest.raises(ReviewError) as error:
        store.record_correspondence("R", {**payload, "request_id": "A2"})
    assert error.value.code == "correspondence_conflict"
    assert error.value.details["correspondence_revision"] == first["event"]["revision"]
    fresh = {**payload, "request_id": "A2", "expected_correspondence_revision": first["event"]["revision"], "reason": "Explicit second human record after inspecting current mapping"}
    second = store.record_correspondence("R", fresh)
    assert second["event"]["previous_revision"] == first["event"]["revision"]
    assert second["matrix_generation"] == first["matrix_generation"]


@pytest.mark.parametrize("mutation", ["current", "old_assessment", "new_artifact", "unknown_row", "retired_row", "duplicate", "actor", "foreign_history"])
def test_stale_unknown_cross_run_and_untrusted_selections_fail_without_event(comparison, mutation):
    """what_bug_this_catches: old source/cross-run IDs, retired targets, duplicate rows or forged actors entering correspondence history."""
    store, wrapper = comparison
    payload = _payload(wrapper)
    if mutation == "current":
        payload["expected_matrix_generation"] = "0" * 64
    elif mutation == "old_assessment":
        payload["expected_old_assessment_generation"] = "0" * 64
    elif mutation == "new_artifact":
        payload["expected_new_artifact_sha"] = "0" * 64
    elif mutation == "unknown_row":
        payload["new_rows"] = [{"row_id": "not-registered", "row_revision": "0" * 64}]
    elif mutation == "retired_row":
        retired = next(row for row in wrapper["comparison"]["new_rows"] if row["retired"])
        payload["new_rows"] = [{"row_id": retired["row_id"], "row_revision": retired["row_revision"]}]
    elif mutation == "duplicate":
        payload["old_rows"] *= 2
    elif mutation == "actor":
        payload["actor"] = "forged"
    else:
        foreign_artifact = parse_text_pages(["The vendor must submit a fictional foreign-run worksheet."],
            source_id="synthetic-foreign", terms="Self-authored fictional development fixture")
        corpus = make_corpus([])
        foreign = store.create_run("OTHER", foreign_artifact, corpus, build_matrix(foreign_artifact, corpus)["rows"])
        payload["expected_old_matrix_generation"] = foreign["matrix_generation"]
    before = store.load_run("R")
    with pytest.raises(ValueError) as error:
        store.record_correspondence("R", payload)
    assert getattr(error.value, "code", None)
    assert store.correspondence_history("R")["total_events"] == 0 and store.load_run("R") == before


@pytest.mark.parametrize("relation,old_count,new_count", [("one_to_one",1,1),("split",1,2),("merge",2,1),("added",0,2),("removed",2,0)])
def test_supported_relation_cardinalities_only_record_lineage(comparison, relation, old_count, new_count):
    """what_bug_this_catches: split/merge/add/remove being treated as automatic requirement edits or approval inheritance."""
    store, wrapper = comparison
    before = store.load_run("R")
    event = store.record_correspondence("R", _payload(wrapper, relation=relation, old_count=old_count, new_count=new_count))["event"]
    assert event["relation"] == relation and len(event["old_rows"]) == old_count and len(event["new_rows"]) == new_count
    assert store.load_run("R") == before and event["approval_carry_allowed"] is False


def test_interrupted_event_assembly_rolls_back_placeholder_and_can_retry(comparison, monkeypatch):
    """what_bug_this_catches: crash between SQL sequence allocation and final event leaving partial durable JSON/idempotency state."""
    store, wrapper = comparison
    original = store.transaction

    class CrashConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE correspondence_events SET event="):
                raise RuntimeError("Synthetic interruption before final event commit")
            return self.connection.execute(sql, parameters)

    @contextmanager
    def interrupted(run_id):
        with original(run_id) as connection:
            yield CrashConnection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(store, "transaction", interrupted)
        with pytest.raises(RuntimeError):
            store.record_correspondence("R", _payload(wrapper))
    assert store.correspondence_history("R")["total_events"] == 0
    assert store.record_correspondence("R", _payload(wrapper))["replayed"] is False


def test_mapping_backup_restore_preserves_lineage_and_corruption_is_visible(comparison, tmp_path):
    """what_bug_this_catches: backup/restore omitting human mappings or accepting an altered event chain as consistent."""
    store, wrapper = comparison
    event = store.record_correspondence("R", _payload(wrapper))["event"]
    store.backup(tmp_path / "backup")
    restore_store(tmp_path / "backup", tmp_path / "restored")
    restored = ReviewStore(tmp_path / "restored")
    assert restored.correspondence_history("R")["events"] == [event]
    assert restored.load_run("R")["matrix_generation"] == event["new_identity"]["matrix_generation"]
    assert diagnostics(restored.root)["ok"] is True
    corrupted = deepcopy(event)
    corrupted["reason"] = "Tampered after commit without updating digest"
    with restored.transaction("R") as connection:
        connection.execute("UPDATE correspondence_events SET event=? WHERE event_id=?", (json.dumps(corrupted), event["event_id"]))
    report = diagnostics(restored.root)
    assert report["ok"] is False and report["domain"] == "correspondence_history_corrupt"
