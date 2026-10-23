"""Export regression SOURCE ONLY; unrun under Dirty Mode."""
from copy import deepcopy
import csv
import io
import json

import pytest

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.export import export_matrix, verify_export
from rfp_matrix.export import bundle
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.source import coverage_page_sha, parse_text_pages


@pytest.fixture
def matrix(tmp_path):
    artifact = parse_text_pages(["The vendor must provide encrypted daily backups.\nThe vendor must provide audit log exports."],
        source_id="synthetic-export", terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    store = ReviewStore(tmp_path / "state")
    snapshot = store.create_run("R", artifact, corpus, build_matrix(artifact, corpus)["rows"])
    row = snapshot["rows"][0]
    event = store.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=snapshot["assessment_generation"],
                         expected_row_revision=row.get("decision_revision", row["row_revision"]), request_id="gap1", reason="Acknowledge corpus absence only")
    return store, store.load_run("R"), event


def test_export_retries_keep_exact_bytes_key_coverage_and_unresolved_rows(matrix, tmp_path):
    """what_bug_this_catches: non-deterministic retry bytes or an export silently dropping unresolved obligations/audit."""
    store, snapshot, _ = matrix
    output = tmp_path / "exports"
    kwargs = dict(request_id="export1", expected_matrix_generation=snapshot["matrix_generation"])
    first = export_matrix(store, "R", output, **kwargs)
    directory = output / first["export_id"]
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    second = export_matrix(store, "R", output, **kwargs)
    assert second["export_id"] == first["export_id"]
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    manifest = json.loads(before["manifest.json"])
    assert manifest["row_count"] == 2 and len(manifest["unresolved_inventory"]) == 1
    assert "coverage.json" in manifest["artifacts"]
    assert json.loads(before["coverage.json"])["coverage"] == manifest["coverage"]
    assert verify_export(directory)["status"] == "structurally_verified"
    with pytest.raises(ReviewError) as error:
        export_matrix(store, "R", output, request_id="export1", expected_matrix_generation="0" * 64)
    assert error.value.code == "idempotency_conflict"


def test_coverage_change_during_render_returns_historical_not_current(matrix, tmp_path, monkeypatch):
    """what_bug_this_catches: a captured approval/export claiming currentness after an intervening coverage decision."""
    store, snapshot, event = matrix
    render = bundle._render

    def racing_render(frozen, formats):
        result = render(frozen, formats)
        store.inspect_coverage("R", 0, "flag_unresolved", expected_artifact_sha=snapshot["artifact"]["artifact_sha"],
            expected_page_hash=coverage_page_sha(snapshot["artifact"], 0), expected_matrix_generation=snapshot["matrix_generation"],
            expected_coverage_revision=None, request_id="race-coverage", reason="Original page now needs human inspection")
        return result

    monkeypatch.setattr(bundle, "_render", racing_render)
    receipt = export_matrix(store, "R", tmp_path / "exports", request_id="race-export",
                             expected_matrix_generation=snapshot["matrix_generation"])
    assert receipt["historical"] is True and receipt["current_at_return"] is False
    live = store.load_run("R")
    assert all(row["approval"] == "stale" for row in live["rows"])
    manifest = json.loads((tmp_path / "exports" / receipt["export_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert event["event_id"] in manifest["approval_event_ids"].values()
    assert manifest["matrix_generation"] == snapshot["matrix_generation"] != live["matrix_generation"]


def test_csv_html_injection_and_positive_gap_columns_do_not_cross(matrix):
    """what_bug_this_catches: spreadsheet formulas/HTML execution or a gap acknowledgement rendered as a positive commitment."""
    store, snapshot, _ = matrix
    frozen = deepcopy(snapshot)
    frozen.update(export_id="X-render-only", export_created_at="fixed", decision_events=store.events("R"),
                  coverage=store.coverage_state("R"), export_render_policy=bundle._AUDIT_RENDER_POLICY,
                  inventory_review={"status": "unavailable", "audit": {}, "promotions": [], "historical_promotions": []})
    # Renderer-only hostile content; this fixture asserts escaping, not source validity.
    frozen["rows"][1]["answer_text"] = '<script>alert("example")</script>'
    frozen["rows"][1]["unvalidated_note"] = " \t=HYPERLINK(\"example\")"
    rendered = bundle._render(frozen, ("csv", "html", "md"))
    csv_rows = list(csv.DictReader(io.StringIO(rendered["matrix.csv"].decode())))
    assert csv_rows[0]["positive_commitment"] == "" and csv_rows[0]["gap_acknowledgement"]
    assert csv_rows[1]["positive_commitment"] == csv_rows[1]["gap_acknowledgement"] == ""
    assert csv_rows[1]["unvalidated_note"].startswith("'")
    html = rendered["matrix.html"].decode()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "default-src 'none'" in html and "Frozen historical snapshot" in html
    assert "&lt;script&gt;" in rendered["matrix.md"].decode()
    forged = deepcopy(frozen)
    forged["rows"][0]["approval"] = "approved_positive"
    with pytest.raises(ReviewError) as error:
        bundle._validate_snapshot(store, forged)
    assert error.value.code == "approval_lineage_invalid"


def test_legacy_renderer_retries_do_not_gain_new_audit_files(matrix):
    """what_bug_this_catches: a renderer upgrade changing bytes for already-persisted legacy export intents."""
    store, snapshot, _ = matrix
    legacy = deepcopy(snapshot)
    legacy.update(export_id="legacy", export_created_at="fixed", decision_events=store.events("R"))
    rendered = bundle._render(legacy, ("csv",))
    assert set(rendered) == {"matrix.csv"}
    assert "coverage_revision" not in next(csv.reader(io.StringIO(rendered["matrix.csv"].decode())))
