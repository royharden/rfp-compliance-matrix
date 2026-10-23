"""Optional browser application regression SOURCE ONLY; unrun in Dirty Mode."""
import hashlib

import pytest

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.review.application import ReviewerApplication
from rfp_matrix.review.server import serve_local
from rfp_matrix.source import SourceRegistry, coverage_page_sha, parse_text_pages


@pytest.fixture
def app(tmp_path):
    artifact = parse_text_pages(["The vendor must provide encrypted daily backups."],
        source_id="synthetic-application", terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    store = ReviewStore(tmp_path / "state")
    SourceRegistry(store.root / "sources").save(artifact)
    store.create_run("R", artifact, corpus, build_matrix(artifact, corpus)["rows"])
    return ReviewerApplication(store)


def test_http_shaped_payload_cannot_select_actor_destination_or_provider(app):
    """what_bug_this_catches: untrusted HTTP fields overriding local actor, output root or provider execution."""
    snapshot = app.get_run("R")
    row = snapshot["rows"][0]
    decision = {"action": "defer", "expected_assessment_generation": snapshot["assessment_generation"],
                "expected_row_revision": row.get("decision_revision", row["row_revision"]), "request_id": "D1", "actor": "forged"}
    with pytest.raises(ReviewError) as error:
        app.decide("R", row["row_id"], decision)
    assert error.value.code == "invalid_payload" and app.store.events("R") == []
    for field in ("out", "actor", "provider_config", "module"):
        with pytest.raises(ReviewError) as error:
            app.export("R", {"expected_matrix_generation": snapshot["matrix_generation"], "request_id": "X1", field: "untrusted"})
        assert error.value.code == "invalid_payload"
    assert app.reviewer_subject.startswith("reviewer-")
    assert app.source_context("R", row["row_id"])["anchors"][0]["quote"] == row["requirement"]["anchors"][0]["quote"]


def test_browser_decision_returns_committed_event_when_projection_fails(app, monkeypatch):
    """what_bug_this_catches: a browser reporting no decision after its committed event encounters unavailable checkpoints."""
    from rfp_matrix.review import graph

    class UnavailableProjection:
        def __init__(self, *_):
            pass

        def apply(self, *_):
            raise ReviewError("checkpoint_corrupt", "Synthetic damaged checkpoint")

    monkeypatch.setattr(graph, "LangGraphReviewDriver", UnavailableProjection)
    snapshot = app.get_run("R")
    row = snapshot["rows"][0]
    result = app.decide("R", row["row_id"], {"action": "defer", "expected_assessment_generation": snapshot["assessment_generation"],
        "expected_row_revision": row.get("decision_revision", row["row_revision"]), "request_id": "D1", "reason": "Inspect later"})
    assert result["event"]["actor"] == app.store.actor
    assert result["reconcile"]["status"] == "checkpoint_unavailable"
    assert result["snapshot"]["rows"][0]["decision_event_id"] == result["event"]["event_id"]
    assert result["snapshot"]["decision_events"] == [result["event"]]


def test_download_is_committed_scoped_hashed_and_explicitly_historical(app):
    """what_bug_this_catches: arbitrary file access, wrong-run exports, stale artifacts labelled current, or tampered bytes."""
    snapshot = app.get_run("R")
    receipt = app.export("R", {"expected_matrix_generation": snapshot["matrix_generation"], "request_id": "X1", "formats": ["html"]})
    downloaded = app.download_export("R", "X1", "matrix.html")
    assert downloaded["current"] is True and downloaded["sha256"] == hashlib.sha256(downloaded["content"]).hexdigest()
    with pytest.raises(ReviewError) as error:
        app.export_file("R", receipt["export_id"], "../review.sqlite3")
    assert error.value.code == "invalid_artifact"
    with pytest.raises(ReviewError) as error:
        app.download_export("R", "unknown-request", "matrix.html")
    assert error.value.code == "export_missing"
    app.store.create_run("OTHER", snapshot["artifact"], snapshot["corpus"], build_matrix(snapshot["artifact"], snapshot["corpus"])["rows"])
    with pytest.raises(ReviewError) as error:
        app.download_export("OTHER", "X1", "matrix.html")
    assert error.value.code == "export_missing"
    app.inspect_coverage("R", {"page_index": 0, "action": "flag_unresolved", "expected_artifact_sha": snapshot["artifact"]["artifact_sha"],
        "expected_page_hash": coverage_page_sha(snapshot["artifact"], 0), "expected_matrix_generation": snapshot["matrix_generation"],
        "expected_coverage_revision": None, "request_id": "C1", "reason": "Reinspect original"})
    assert app.download_export("R", "X1", "matrix.html")["current"] is False
    with pytest.raises(ReviewError) as error:
        app.export_file("R", receipt["export_id"], "matrix.html")
    assert error.value.code == "historical_export"
    (app.export_root / receipt["export_id"] / "matrix.html").write_bytes(b"tampered")
    with pytest.raises(ReviewError) as error:
        app.download_export("R", "X1", "matrix.html")
    assert error.value.code == "artifact_corrupt"


@pytest.mark.parametrize("port", [0, 20178, 20179, 55490, 65535])
def test_serve_requires_claimed_allowed_port_before_any_optional_server_activity(app, port, monkeypatch):
    """what_bug_this_catches: implicit/default/forbidden listener startup or login capability display before port gates."""
    import rfp_matrix.review.server as server

    def forbidden(*_, **__):
        raise AssertionError("No capability display or server execution on a rejected port")

    monkeypatch.setattr(server, "_announce_private_delivery", forbidden)
    with pytest.raises(ReviewError) as error:
        serve_local(app.store, port=port, port_claim_reference="synthetic-unverified-reference", run_ids=["R"], private_login_delivery=True)
    assert error.value.code == "claimed_port_required"
