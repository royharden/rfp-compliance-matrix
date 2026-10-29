"""UNRUN template regressions. Future renders are source definitions, not evidence."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest

from rfp_matrix.web.presentation import build_view, coverage_view, matrix_view, row_view

# Optional `web` extra: skip this module instead of aborting keyless collection.
jinja2 = pytest.importorskip("jinja2")
Environment, FileSystemLoader, StrictUndefined = jinja2.Environment, jinja2.FileSystemLoader, jinja2.StrictUndefined

HOSTILE = '<script>alert("rfp")</script><img src=x onerror="alert(1)"> & "quoted"'


@pytest.fixture
def environment():
    root = Path(__file__).resolve().parents[3] / "src/rfp_matrix/web/templates"
    return Environment(loader=FileSystemLoader(str(root)), autoescape=True, undefined=StrictUndefined)


def render(environment, name, vm):
    return environment.get_template(name).render(request=None, vm=vm, csrf_token="fixture-csrf",
        request_id="fixture-request", runtime_unverified=True)


def snapshot_row():
    pointer = {"source_id": "source", "source_sha": "a" * 64, "artifact_sha": "b" * 64,
               "page_index": 0, "start": 0, "end": 4, "quote": HOSTILE, "excerpt_sha": "c" * 64,
               "section_id": "section", "record_id": "vendor", "corpus_generation": "d" * 64,
               "sentence_id": "vendor:0:4", "broad_fallback": False}
    coverage = {"matrix_generation": "e" * 64, "effective": {"artifact_sha": "b" * 64,
        "complete": True, "pages": [{"page_index": 0, "page_sha": "f" * 64,
            "original_coverage": "ok", "resolved": True, "issues": []}],
        "unresolved_pages": [], "artifact_issues": [], "audit_revisions": []}}
    row = {"row_id": "row-1", "row_revision": "1" * 64, "assessment_generation": "2" * 64,
           "approval": "pending", "answer_text": HOSTILE, "decision_event_id": None,
           "requirement": {"requirement_id": "req", "revision": "3" * 64,
               "literal_clause": HOSTILE, "interpretation": HOSTILE, "mandatory": True,
               "conditional": False, "administrative": True, "applicability": "applies",
               "condition": None, "uncertainty": [], "anchors": [pointer], "child_ids": []},
           "assessment": {"status": "supported", "complete": True, "method": "B0a", "origin": "baseline",
               "model": HOSTILE, "reason": HOSTILE, "support": [pointer], "counterevidence": []},
           "validation": {"state": "pending", "method": "restricted_template"}}
    snapshot = {"run_id": "run-1", "matrix_generation": "e" * 64, "assessment_generation": "2" * 64,
                "corpus": {"complete": True}, "coverage": coverage, "rows": [row],
                "policy_status": {"status": "current", "allows_current_claim": True, "reasons": [], "execution_verified": False}}
    return snapshot, row


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms, self.links, self.current = [], [], None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""), "method": attrs.get("method", "get"), "fields": {}}
            self.forms.append(self.current)
        if tag in {"input", "textarea", "select"} and self.current is not None and "name" in attrs:
            self.current["fields"][attrs["name"]] = attrs.get("value", "")
        if tag == "a":
            self.links.append(attrs.get("href", ""))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def test_hostile_source_evidence_model_and_draft_autoescaped(environment):
    """what_bug_this_catches: RFP/model or textarea closing markup becomes executable HTML."""
    snapshot, row = snapshot_row()
    context = {"anchors": [{**row["requirement"]["anchors"][0], "context": HOSTILE}]}
    for name, vm in (("row.html", row_view(snapshot, row, context=context)), ("matrix.html", matrix_view(snapshot))):
        html = render(environment, name, vm)
        assert HOSTILE not in html
        assert "&lt;script&gt;" in html
        assert "<script>" not in html
        assert "<img src=x" not in html


def test_decision_forms_closed_and_version_bound(environment):
    """what_bug_this_catches: UI drops CAS/CSRF or accepts actor/path/provider policy fields."""
    snapshot, row = snapshot_row()
    forms = Forms(render(environment, "row.html", row_view(snapshot, row)))
    decisions = [f for f in forms.forms if f["action"].endswith("/decide")]
    assert decisions
    allowed = {"csrf_token", "request_id", "action", "expected_assessment_generation", "expected_row_revision", "reason", "answer_text", "human_validated"}
    for form in decisions:
        assert set(form["fields"]) <= allowed
        assert form["method"] == "post"
        assert form["fields"]["csrf_token"] == "fixture-csrf"
        assert form["fields"]["expected_row_revision"] == "1" * 64
        assert form["fields"]["expected_assessment_generation"] == "2" * 64
    assert all(link.startswith(("/", "#")) for link in forms.links)
    assert 'name="human_validated" value="true" required' in render(environment, "row.html", row_view(snapshot, row))


@pytest.mark.parametrize("retired,historical", [(True, False), (False, True)])
def test_stale_retired_controls_and_current_commitment_absent(environment, retired, historical):
    """what_bug_this_catches: historical/retired positive record presents an actionable approval."""
    snapshot, row = snapshot_row()
    snapshot["historical"], row["retired"] = historical, retired
    row.update(approval="approved_positive", decision_event_id="event")
    row["validation"]["state"] = "validated"
    html = render(environment, "row.html", row_view(snapshot, row))
    assert not [f for f in Forms(html).forms if f["action"].endswith("/decide")]
    assert "NO CURRENT APPROVED COMMITMENT" in html


def test_gap_acknowledgment_cannot_populate_positive_commitment(environment):
    """what_bug_this_catches: accepted absence/conflict is displayed as compliance."""
    snapshot, row = snapshot_row()
    row.update(approval="approved_gap", decision_event_id="gap-event")
    row["assessment"].update(status="cannot_answer_absent", support=[])
    html = render(environment, "row.html", row_view(snapshot, row))
    assert "Gap acknowledged" in html
    assert "NO CURRENT APPROVED COMMITMENT" in html
    assert 'name="action" value="approve_positive"' not in html
    assert 'name="action" value="accept_gap"' in html


def test_empty_workspaces_and_zero_rows_do_not_disappear(environment):
    """what_bug_this_catches: empty inventory/zero count is mistaken for missing data or success."""
    assert "No authorized runs" in render(environment, "runs.html", build_view("runs", {"runs": []}))
    vm = build_view("runs", {"runs": [{"run_id": "empty", "row_count": 0}]})
    assert "0 inventory rows" in render(environment, "runs.html", vm)
    snapshot, _ = snapshot_row()
    snapshot["rows"] = []
    assert "No rows in this queue" in render(environment, "matrix.html", matrix_view(snapshot))


def test_unsupported_coverage_has_no_acceptance_options_and_exact_fields(environment):
    """what_bug_this_catches: scanned/image page can be cleared or page revision is omitted."""
    snapshot, _ = snapshot_row()
    envelope = deepcopy(snapshot["coverage"])
    envelope["effective"].update(complete=False, unresolved_pages=[0])
    envelope["effective"]["pages"][0].update(original_coverage="unsupported", resolved=False, issues=["image_without_native_text"])
    vm = {**coverage_view(envelope), "run_id": "run-1", "matrix_generation": "e" * 64}
    html = render(environment, "coverage.html", vm)
    assert 'value="accept_blank"' not in html and 'value="accept_text"' not in html
    form = next(f for f in Forms(html).forms if f["action"].endswith("/coverage/inspect"))
    assert set(form["fields"]) == {"csrf_token", "request_id", "page_index", "expected_artifact_sha", "expected_page_hash", "expected_matrix_generation", "expected_coverage_revision", "action", "inspected_original", "reason"}
    assert form["fields"]["page_index"] == "0"
    assert form["fields"]["expected_coverage_revision"] == ""
    assert "UNRESOLVED" in html


def test_artifact_notice_rendered_escaped_as_neutral_meta_not_warning(environment):
    """what_bug_this_catches: a non-blocking source notice renders as markup, as a warning, or disables inspection."""
    snapshot, _ = snapshot_row()
    envelope = deepcopy(snapshot["coverage"])
    envelope["effective"]["artifact_notices"] = [HOSTILE]
    vm = {**coverage_view(envelope), "run_id": "run-1", "matrix_generation": "e" * 64}
    html = render(environment, "coverage.html", vm)
    assert HOSTILE not in html and "&lt;script&gt;" in html
    assert '<aside class="meta" aria-label="Source notices">' in html
    assert 'aria-label="Review warnings"' not in html
    assert "complete under the recorded policy" in html
    assert any(f["action"].endswith("/coverage/inspect") for f in Forms(html).forms)


def test_login_password_not_echoed_and_form_is_closed(environment):
    """what_bug_this_catches: one-time credential appears in HTML/value/URL after a retry."""
    html = render(environment, "login.html", {"credential": HOSTILE})
    form = Forms(html).forms[0]
    assert set(form["fields"]) == {"csrf_token", "credential"}
    assert form["action"] == "/login" and form["method"] == "post"
    assert HOSTILE not in html and 'type="password"' in html
    assert form["fields"]["credential"] == ""


def test_exports_ignore_incoming_urls_and_unknown_files(environment):
    """what_bug_this_catches: a report-controlled URL or filename becomes a download link."""
    vm = {"run_id": "run-1", "matrix_generation": "e" * 64, "exports": [{
        "export_id": "X-" + "a" * 32, "created_at": "fixture-time", "state": "published", "historical": True,
        "matrix_generation": "old", "downloads": [{"filename": "matrix.html", "url": "https://evil.invalid"},
                                                    {"filename": "secret.txt", "url": "javascript:alert(1)"}]}]}
    html = render(environment, "exports.html", vm)
    assert "evil.invalid" not in html and "secret.txt" not in html and "javascript:" not in html
    assert "HISTORICAL" in html
    assert any("/export-files/X-" in link and link.endswith("/matrix.html") for link in Forms(html).links)


@pytest.mark.parametrize("code", ["pending", "stale_generation", "conflict", "unavailable"])
def test_error_states_never_infer_success(environment, code):
    """what_bug_this_catches: an unavailable/conflicting action reads like committed approval."""
    html = render(environment, "error.html", {"code": code, "http_status": 409})
    assert code in html
    assert "No successful decision inferred" in html
    assert "No approval or export success" in html


@pytest.mark.parametrize("status", ["missing", "legacy", "mismatch", "restart_required"])
def test_noncurrent_policy_removes_controls_and_current_answer(environment, status):
    """what_bug_this_catches: historical positive decision survives changed/missing semantics."""
    snapshot, row = snapshot_row()
    row.update(approval="approved_positive", decision_event_id="fixture-event")
    row["validation"]["state"] = "validated"
    snapshot["policy_status"] = {"status": status, "allows_current_claim": False,
        "reasons": [HOSTILE], "recorded_fingerprint": "a" * 64, "current_fingerprint": "b" * 64}
    vm = row_view(snapshot, row)
    assert vm["row"]["approved_answer"] is None
    assert vm["controls"]["actions"] == [] and vm["row"]["current"] is False
    for name, view in (("row.html", vm), ("matrix.html", matrix_view(snapshot))):
        html = render(environment, name, view)
        assert f"Semantic policy: {status}" in html
        assert "Current semantic approval cannot be asserted" in html
        assert HOSTILE not in html and "&lt;script&gt;" in html
        assert not any(form["action"].endswith("/decide") for form in Forms(html).forms)


def test_captured_provenance_label_cannot_enable_application_currentness(environment):
    """what_bug_this_catches: recorded descriptor/status string becomes live approval authority."""
    snapshot, row = snapshot_row()
    del snapshot["policy_status"]
    row["assessment"]["policy_provenance"] = {"status": "current", "fingerprint": "a" * 64}
    vm = row_view(snapshot, row)
    assert vm["policy_status"]["status"] == "missing"
    assert vm["controls"]["actions"] == [] and vm["row"]["approved_answer"] is None
    html = render(environment, "row.html", vm)
    assert "Source integrity is not proof of executed assessment" in html
    # Missing provenance forbids row mutations; authenticated sign-out remains
    # available through the base template and cannot establish row currentness.
    forms = Forms(html).forms
    assert len(forms) == 1 and forms[0]["action"] == "/logout"
    assert forms[0]["method"] == "post"
    assert set(forms[0]["fields"]) == {"csrf_token"}


def test_committed_inventory_download_uses_fixed_scoped_route(environment):
    """what_bug_this_catches: audit artifact is hidden or uses caller-controlled path/URL."""
    vm = {"run_id": "run-1", "matrix_generation": "e" * 64, "exports": [{
        "export_id": "X-" + "b" * 32, "created_at": "fixture-time", "state": "published", "historical": True,
        "matrix_generation": "old", "downloads": [{"filename": "inventory-audit.json", "url": "https://evil.invalid"},
            {"filename": "../inventory-audit.json", "url": "javascript:alert(1)"}]}]}
    html = render(environment, "exports.html", vm)
    links = Forms(html).links
    assert f"/runs/run-1/export-files/X-{'b' * 32}/inventory-audit.json" in links
    assert "evil.invalid" not in html and "../inventory-audit.json" not in html
    assert "(historical)" in html


@pytest.mark.parametrize("status,allows", [("missing", True), ("mismatch", True), ("restart_required", True), ("current", False)])
def test_policy_notice_requires_consistent_current_state(environment, status, allows):
    """what_bug_this_catches: a true marker alone suppresses noncurrent-policy warnings."""
    macro = environment.get_template("_parts.html").module.policy_warning
    html = str(macro({"status": status, "allows_current_claim": allows, "reasons": [HOSTILE]}))
    assert 'class="warning"' in html
    assert "Current semantic approval cannot be asserted" in html
    assert HOSTILE not in html and "&lt;script&gt;" in html


def test_missing_typed_policy_is_actionable_without_browser_policy_fields(environment):
    """what_bug_this_catches: missing typed policy is repaired via a model/config HTTP form."""
    snapshot, row = snapshot_row()
    row["assessment"]["method"] = "F1"
    snapshot["policy_status"] = {"status": "missing", "allows_current_claim": False,
        "reasons": [], "action": "supply_review_policy_config", "config": "PRIVATE_CONFIG_SENTINEL",
        "policy_input": "review-policy-identity-only-v1"}
    vm = row_view(snapshot, row)
    html = render(environment, "row.html", vm)
    assert "--review-policy-config" in html and "The browser cannot change policy" in html
    assert "PRIVATE_CONFIG_SENTINEL" not in html
    assert vm["controls"]["actions"] == [] and vm["row"]["approved_answer"] is None
    assert not any(f["action"].endswith("/decide") for f in Forms(html).forms)
    for form in Forms(html).forms:
        assert not set(form["fields"]) & {"provider", "config", "review_policy_config", "allow_live", "actor", "model"}


def test_captured_review_receipt_cannot_self_authorize_currentness(environment):
    """what_bug_this_catches: an assessment receipt or purpose string becomes current authority."""
    snapshot, row = snapshot_row()
    del snapshot["policy_status"]
    row["assessment"].update(method="F1", review_policy_identity={
        "purpose": "review-policy-identity-only-v1", "status": "available", "allows_current_claim": True})
    row.update(approval="approved_positive", decision_event_id="fixture-event")
    row["validation"]["state"] = "validated"
    vm = row_view(snapshot, row)
    assert vm["policy_status"]["allows_current_claim"] is False
    assert vm["row"]["approved_answer"] is None and vm["controls"]["actions"] == []
    html = render(environment, "row.html", vm)
    assert "NO CURRENT APPROVED COMMITMENT" in html
    assert not any(f["action"].endswith("/decide") for f in Forms(html).forms)


def test_matching_policy_does_not_promote_f2_or_change_decision_cas(environment):
    """what_bug_this_catches: read-only policy match calibrates F2 or substitutes content CAS."""
    snapshot, row = snapshot_row()
    row["decision_revision"] = "opaque-decision-cas-v2"
    snapshot["policy_status"]["policy_input"] = "review-policy-identity-only-v1"
    vm = row_view(snapshot, row)
    html = render(environment, "row.html", vm)
    decisions = [f for f in Forms(html).forms if f["action"].endswith("/decide")]
    assert decisions and all(f["fields"]["expected_row_revision"] == "opaque-decision-cas-v2" for f in decisions)
    row["assessment"].update(method="F2", status="needs_review", complete=False)
    row.update(approval="approved_positive", decision_event_id="fixture-event")
    row["validation"]["state"] = "validated"
    f2 = row_view(snapshot, row)
    assert "approve_positive" not in f2["controls"]["actions"]
    assert f2["row"]["approved_answer"] is None
    f2_html = render(environment, "row.html", f2)
    assert 'value="approve_positive"' not in f2_html and "NO CURRENT APPROVED COMMITMENT" in f2_html
