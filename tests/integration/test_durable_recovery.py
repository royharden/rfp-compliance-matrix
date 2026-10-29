"""Recovery regression SOURCE ONLY; no test/SQLite execution in Dirty Mode."""
import json
import sqlite3

import pytest

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.review import LangGraphReviewDriver, ReviewError, ReviewStore
from rfp_matrix.review.operations import diagnostics, restore_store
from rfp_matrix.source import SourceRegistry, parse_text_pages


@pytest.fixture
def durable(tmp_path):
    artifact = parse_text_pages(["The vendor must provide encrypted daily backups."],
        source_id="synthetic-recovery", terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    store = ReviewStore(tmp_path / "state")
    SourceRegistry(store.root / "sources").save(artifact)
    snapshot = store.create_run("R", artifact, corpus, build_matrix(artifact, corpus)["rows"])
    row = snapshot["rows"][0]
    event = store.decide("R", row["row_id"], "defer", expected_assessment_generation=snapshot["assessment_generation"],
                         expected_row_revision=row.get("decision_revision", row["row_revision"]), request_id="durable1", reason="Inspect later")
    return store, event


def test_decision_commits_before_resume_failure_and_restarts_idempotently(durable):
    """what_bug_this_catches: a graph crash rolling back/lossily reissuing an already committed domain decision."""
    store, event = durable

    class CrashBeforeResume:
        def apply(self, *_):
            raise RuntimeError("Synthetic crash after domain commit, before graph confirmation")

    with pytest.raises(RuntimeError):
        store.reconcile("R", CrashBeforeResume())
    reopened = ReviewStore(store.root)
    assert reopened.events("R") == [event]
    assert reopened.pending_intents("R") == [event]
    assert reopened.load_run("R")["rows"][0]["decision_event_id"] == event["event_id"]
    again = reopened.decide("R", event["row_id"], "defer", expected_assessment_generation=event["assessment_generation"],
                             expected_row_revision=event.get("expected_decision_revision", event["expected_row_revision"]), request_id=event["request_id"], reason="Inspect later")
    assert again == event and len(reopened.events("R")) == 1


def test_explicit_lost_checkpoint_epoch_requeues_without_resurrecting_approval(durable):
    """what_bug_this_catches: stale applied markers suppressing projection recovery or creating approval from checkpoints."""
    store, event = durable
    epoch = store.graph_epoch("R")
    store.mark_applied("R", event["event_id"], epoch)
    assert store.pending_intents("R") == [] and store.applied(event["event_id"])
    next_epoch = store.recover_graph_epoch("R", "explicit lost projection recovery", reset_intents=True)
    assert next_epoch == epoch + 1
    assert not store.applied(event["event_id"])
    assert store.pending_intents("R") == [event]
    assert store.load_run("R")["rows"][0]["approval"] == "pending"
    store.mark_applied("R", event["event_id"], next_epoch)
    assert store.applied(event["event_id"]) and not store.pending_intents("R")


def test_new_destination_restore_preserves_domain_and_quarantines_checkpoint(durable, tmp_path):
    """what_bug_this_catches: overwriting active state, losing decisions/blobs, or activating an opaque checkpoint on restore."""
    store, event = durable
    checkpoint = store.root / "checkpoints.sqlite3"
    with sqlite3.connect(checkpoint) as db:
        db.executescript("CREATE TABLE checkpoints(x); CREATE TABLE writes(x);")
    bundle = tmp_path / "backup"
    store.backup(bundle)
    restored_root = tmp_path / "restored"
    result = restore_store(bundle, restored_root)
    restored = ReviewStore(restored_root)
    assert result["checkpoint"] == "quarantined_not_adopted"
    assert restored.events("R") == [event]
    assert restored.store_id == store.store_id
    assert diagnostics(restored_root)["ok"]
    assert (restored_root / "checkpoints.snapshot.sqlite3").is_file()
    assert not (restored_root / "checkpoints.sqlite3").exists()
    assert restored.pending_intents("R") == [event]
    with pytest.raises(ReviewError) as error:
        restore_store(bundle, store.root)
    assert error.value.code == "unsafe_restore_destination"


def test_corrupt_bundle_and_partial_restore_fail_closed(durable, tmp_path):
    """what_bug_this_catches: accepting tampered backup bytes or bootstrapping over a partially copied domain."""
    store, _ = durable
    bundle = tmp_path / "backup"
    store.backup(bundle)
    (bundle / "review.sqlite3").write_bytes(b"corrupted snapshot")
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ReviewError) as error:
        restore_store(bundle, destination)
    assert error.value.code == "backup_corrupt" and not destination.exists()
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "restore-status.json").write_text(json.dumps({"state": "incomplete"}), encoding="utf-8")
    with pytest.raises(ReviewError) as error:
        ReviewStore(partial)
    assert error.value.code == "restore_incomplete"
    assert not (partial / "review.sqlite3").exists()
    absent = tmp_path / "missing-domain"
    absent.mkdir()
    (absent / "store-id.txt").write_text("preserved-identity", encoding="utf-8")
    with pytest.raises(ReviewError) as error:
        ReviewStore(absent)
    assert error.value.code == "domain_missing"


def test_partial_checkpoint_schema_is_never_silently_adopted(tmp_path):
    """what_bug_this_catches: saver setup making an unsupported partial checkpoint look like a successful resume."""
    path = tmp_path / "partial.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(x)")
    with pytest.raises(ReviewError) as error:
        LangGraphReviewDriver._schema(path)
    assert error.value.code == "checkpoint_unsupported"
