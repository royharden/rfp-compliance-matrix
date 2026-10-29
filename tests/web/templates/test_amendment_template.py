"""UNRUN amendment renderer source; authored mappings are development data only."""
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from rfp_matrix.web.presentation import amendments_view

# Optional `web` extra: skip this module instead of aborting keyless collection.
jinja2 = pytest.importorskip("jinja2")
Environment, FileSystemLoader, StrictUndefined = jinja2.Environment, jinja2.FileSystemLoader, jinja2.StrictUndefined

HOSTILE = '<script>alert("amendment")</script><a href="javascript:alert(1)">source</a>'


@pytest.fixture
def environment():
    root = Path(__file__).resolve().parents[3] / "src/rfp_matrix/web/templates"
    return Environment(loader=FileSystemLoader(str(root)), autoescape=True, undefined=StrictUndefined)


def render(environment, vm):
    return environment.get_template("amendments.html").render(request=None, vm=vm,
        csrf_token="fixture-csrf", request_id="fixture-request", runtime_unverified=True)


def comparison():
    def identity(character):
        return {key: character * 64 for key in ("artifact_sha", "raw_sha", "matrix_generation",
                "assessment_generation", "corpus_generation", "snapshot_pin")}
    def row(key):
        return {"row_id": key, "row_revision": "c" * 64, "requirement_revision": "d" * 64,
                "requirement_id": key, "literal_excerpt": HOSTILE, "literal_sha": "e" * 64,
                "mandatory": True, "conditional": True, "administrative": False,
                "applicability": "unknown", "condition": HOSTILE, "anchors": [{
                    "source_id": "source", "source_sha": "a" * 64, "artifact_sha": "b" * 64,
                    "page_index": 0, "start": 0, "end": 20, "quote_excerpt": HOSTILE,
                    "context": HOSTILE, "excerpt_sha": "f" * 64}], "row_pin": "e" * 64}
    old, new = [row("old-a"), row("old-b")], [row("new-a"), row("new-b")]
    pairs = [("old-a", "new-a"), ("old-a", "new-b"), ("old-b", "new-a")]
    candidates = [{"candidate_id": str(index) * 64, "old_row_id": left,
        "new_row_id": right, "old_row_revision": "c" * 64, "new_row_revision": "c" * 64,
        "lexical_score": .5, "literal_changed": True, "ambiguity": {"split_possible": True,
        "merge_possible": True}, "field_changes": {"condition": {"old": HOSTILE, "new": HOSTILE}}}
        for index, (left, right) in enumerate(pairs, 1)]
    return {"schema_version": 1, "run_id": "run-1", "comparison_id": "a" * 64,
            "report_sha": "b" * 64, "old_identity": identity("1"), "new_identity": identity("2"),
            "old_rows": old, "new_rows": new, "candidates": candidates,
            "unmapped_mandatory_old_ids": ["old-b"], "unmapped_mandatory_new_ids": ["new-b"],
            "counts": {"old_active": 2, "new_active": 2}, "human_events": []}


class FormValues(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms, self.current, self.selected = [], None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""), "fields": {}, "selects": {}}
            self.forms.append(self.current)
        if self.current is not None:
            if tag in {"input", "textarea"} and "name" in attrs:
                self.current["fields"][attrs["name"]] = attrs.get("value", "")
            if tag == "select":
                self.selected = attrs["name"]
                self.current["selects"][self.selected] = []
            if tag == "option" and self.selected:
                self.current["selects"][self.selected].append(attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "select":
            self.selected = None
        if tag == "form":
            self.current, self.selected = None, None


def correspondence_form(html):
    return next(form for form in FormValues(html).forms if form["action"].endswith("/amendments/correspondence"))


def test_before_after_conditions_context_and_human_reason_are_escaped(environment):
    """what_bug_this_catches: hostile amendment literals/conditions/history become trusted markup."""
    report = comparison()
    report["human_events"] = [{"event_id": "event", "actor": "fixture-human", "reason": HOSTILE,
        "relation": "one_to_one", "comparison_id": report["comparison_id"], "created_at": "fixture-time"}]
    html = render(environment, amendments_view(report))
    assert HOSTILE not in html and "&lt;script&gt;" in html
    assert "<script>" not in html and 'href="javascript:' not in html
    assert "UNCONFIRMED" in html
    assert "Separately recorded human lineage history" in html
    assert "no approval carried" in html


def test_form_closed_all_versions_and_registered_json_selections(environment):
    """what_bug_this_catches: lineage CAS/version omitted, arbitrary JSON accepted, or approval submitted."""
    vm = amendments_view(comparison())
    vm["controls"]["expected_correspondence_revision"] = None
    form = correspondence_form(render(environment, vm))
    expected = {"csrf_token", "request_id", "reason", "expected_correspondence_revision",
        "expected_old_matrix_generation", "expected_matrix_generation", "expected_old_assessment_generation",
        "expected_new_assessment_generation", "expected_old_artifact_sha", "expected_new_artifact_sha"}
    assert set(form["fields"]) == expected
    assert form["fields"]["expected_correspondence_revision"] == ""
    assert form["fields"]["expected_old_artifact_sha"] == "1" * 64
    assert form["fields"]["expected_new_artifact_sha"] == "2" * 64
    assert set(form["selects"]) == {"relation", "old_rows", "new_rows"}
    assert set(form["selects"]["relation"]) == {"one_to_one", "split", "merge", "added", "removed"}
    for side in ("old", "new"):
        registered = {(row["row_id"], row["row_revision"]) for row in vm[side + "_rows"]}
        options = [json.loads(value) for value in form["selects"][side + "_rows"]]
        assert any(len(option) == 2 for option in options)
        for option in options:
            assert len(option) <= 50
            assert all(set(row) == {"row_id", "row_revision"} for row in option)
            assert {(row["row_id"], row["row_revision"]) for row in option} <= registered
    assert 'name="action"' not in render(environment, vm)
    assert 'name="comparison_id"' not in render(environment, vm)


@pytest.mark.parametrize("mode", ["empty", "disabled", "missing_versions"])
def test_empty_stale_unknown_states_have_no_correspondence_form(environment, mode):
    """what_bug_this_catches: missing/disabled/stale comparison permits mutation or implies unchanged source."""
    report = comparison()
    if mode == "empty":
        report = {}
    elif mode == "missing_versions":
        report["new_identity"] = {}
    vm = amendments_view(report)
    if mode == "disabled":
        vm["controls"]["enabled"] = False
    html = render(environment, vm)
    assert not [f for f in FormValues(html).forms if f["action"].endswith("/correspondence")]
    assert "Correspondence controls unavailable" in html
    assert "PROPOSALS ONLY" in html


def test_unknown_candidate_withheld_and_unmapped_mandatory_inventory_visible(environment):
    """what_bug_this_catches: unknown pair creates selectable IDs or mandatory omissions disappear."""
    report = comparison()
    report["candidates"].append({"candidate_id": "unknown", "old_row_id": "UNKNOWN-PRIVATE-PATH",
        "new_row_id": "new-a", "old_row_revision": "c" * 64, "new_row_revision": "c" * 64})
    html = render(environment, amendments_view(report))
    assert "UNKNOWN-PRIVATE-PATH" not in html
    assert "Unmapped mandatory inventory" in html
    assert "old-b" in html and "new-b" in html


def test_existing_lineage_revision_retained_without_approval(environment):
    """what_bug_this_catches: second event silently uses first-event null CAS or offers reapproval."""
    vm = amendments_view(comparison())
    vm["controls"]["expected_correspondence_revision"] = "d" * 64
    form = correspondence_form(render(environment, vm))
    assert form["fields"]["expected_correspondence_revision"] == "d" * 64
    assert "approve_positive" not in form["selects"]["relation"]
    assert vm["approval_carry_allowed"] is False
