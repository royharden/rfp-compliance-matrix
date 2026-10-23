"""Web/domain seam regression SOURCE ONLY; all cases unrun in Dirty Mode."""
import builtins
import json

import pytest

from rfp_matrix import cli
from rfp_matrix.review import ReviewError
from rfp_matrix.review.server import serve_local
from rfp_matrix.source import coverage_page_sha


def test_run_shapes_ownership_and_closed_actor_payload(application):
    """what_bug_this_catches: guessed list shapes, a row from another run, or HTTP actor/provider/path injection."""
    app = application
    listing = app.list_runs()
    assert set(listing) == {"store_id", "runs", "total_runs", "truncated"}
    assert [run["run_id"] for run in listing["runs"]] == ["R1", "R2"]
    first, second = app.get_run("R1"), app.get_run("R2")
    with pytest.raises(ReviewError) as error:
        app.get_row("R1", second["rows"][0]["row_id"])
    assert error.value.code == "row_missing"
    selected = app.list_rows("R1", "unresolved")
    assert selected["matrix_generation"] == first["matrix_generation"] and len(selected["rows"]) == 1
    row = first["rows"][0]
    base = {"action": "defer", "expected_assessment_generation": first["assessment_generation"],
            "expected_row_revision": row.get("decision_revision", row["row_revision"]), "request_id": "D1"}
    for extra in ("actor", "provider_config", "output_dir", "module"):
        with pytest.raises(ReviewError) as error:
            app.decide("R1", row["row_id"], {**base, extra: "untrusted"})
        assert error.value.code == "invalid_payload"
    assert app.store.events("R1") == []


def test_decision_cas_advances_even_without_text_change_and_exact_retry_replays(application, monkeypatch):
    """what_bug_this_catches: reject/defer leaving row CAS unchanged and permitting an unseen later decision to be overwritten."""
    app = application
    monkeypatch.setattr(app, "resume", lambda _: {"status": "checkpoint_unavailable", "domain_decisions_preserved": True})
    before = app.get_run("R1")
    row = before["rows"][0]
    payload = {"action": "reject", "expected_assessment_generation": before["assessment_generation"],
               "expected_row_revision": row.get("decision_revision", row["row_revision"]), "request_id": "D1", "reason": "Needs source review"}
    result = app.decide("R1", row["row_id"], payload)
    current = result["snapshot"]["rows"][0]
    assert current["row_revision"] == row["row_revision"]
    assert current["decision_revision"] != row["decision_revision"]
    assert result["event"]["actor"] == app.store.actor
    assert app.decide("R1", row["row_id"], payload)["event"] == result["event"]
    with pytest.raises(ReviewError) as error:
        app.decide("R1", row["row_id"], {**payload, "request_id": "D2", "action": "defer"})
    assert error.value.code == "stale_revision"
    assert error.value.details["decision_revision"] == current["decision_revision"]
    with pytest.raises(ReviewError) as error:
        app.decide("R1", row["row_id"], {**payload, "reason": "Different intent"})
    assert error.value.code == "idempotency_conflict" and len(app.store.events("R1")) == 1


def test_coverage_event_keeps_pure_fields_nested_and_envelope_actor_owned(application):
    """what_bug_this_catches: HTTP/envelope timestamps leaking into strict pure events or caller-selected coverage identity."""
    app = application
    snapshot = app.get_run("R1")
    payload = {"page_index": 1, "action": "accept_blank", "expected_artifact_sha": snapshot["artifact"]["artifact_sha"],
        "expected_page_hash": coverage_page_sha(snapshot["artifact"], 1), "expected_matrix_generation": snapshot["matrix_generation"],
        "expected_coverage_revision": None, "request_id": "C1", "reason": "Inspected the exact blank original", "inspected_original": True}
    result = app.inspect_coverage("R1", payload)
    assert result["event"]["actor"] == app.store.actor and result["event_id"] and result["created_at"]
    assert "created_at" not in result["event"] and "event_id" not in result["event"]
    assert app.inspect_coverage("R1", payload)["event"] == result["event"]
    with pytest.raises(ReviewError) as error:
        app.inspect_coverage("R1", {**payload, "actor": "forged"})
    assert error.value.code == "invalid_payload"
    assert all(row["approval"] == "stale" for row in app.get_run("R1")["rows"])


def test_unpublished_receipt_download_is_unavailable(application):
    """what_bug_this_catches: an interrupted export stage being downloaded before durable publication."""
    app = application
    snapshot = app.get_run("R1")
    app.export("R1", {"expected_matrix_generation": snapshot["matrix_generation"], "request_id": "X1"})
    with app.store.transaction("R1") as connection:
        connection.execute("UPDATE export_intents SET state='rendered' WHERE run_id='R1' AND request_id='X1'")
    with pytest.raises(ReviewError) as error:
        app.download_export("R1", "X1", "matrix.html")
    assert error.value.code == "export_unavailable"


def test_core_json_review_survives_missing_web_extras_and_serve_fails_without_install(application, monkeypatch, capsys):
    """what_bug_this_catches: eager optional web imports breaking the CLI floor or a missing web extra triggering fallback startup."""
    original_import = builtins.__import__

    def no_web(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"uvicorn", "fastapi", "jinja2"}:
            raise ImportError("Synthetic absent optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_web)
    monkeypatch.setattr(cli, "_store", lambda _: application.store)
    args = cli.build_parser().parse_args(["--state-dir", str(application.store.root), "review", "--run", "R1"])
    assert cli.dispatch(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "ok" and response["data"]["rows"]
    with pytest.raises(ReviewError) as error:
        serve_local(application.store, port=22555, port_claim_reference="synthetic-reference-not-a-real-claim",
                    run_ids=["R1"], private_login_delivery=True)
    assert error.value.code == "optional_web_unavailable"
