"""UNRUN fake-context inventory template contracts; no renderer or audit executed."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from rfp_matrix.web.presentation import inventory_audit_view

HOSTILE = '<script>alert("inventory")</script><img src=x onerror="alert(1)">'


@pytest.fixture
def environment():
    return Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[3] / "src/rfp_matrix/web/templates")),
                       autoescape=True, undefined=StrictUndefined)


def payload():
    anchor = {"source_id": "source", "source_sha": "b" * 64, "artifact_sha": "a" * 64,
              "page_index": 0, "start": 0, "end": 4, "quote": HOSTILE, "excerpt_sha": "c" * 64}
    entries = [{"candidate_id": "candidate-" + kind, "disposition": kind, "reason": HOSTILE,
        "machine_disposition": "retained" if kind == "retained" else "rejected", "machine_reason": HOSTILE,
        "literal_clause": HOSTILE, "anchor": anchor, "mandatory": True, "conditional": False,
        "administrative": True, "promotable": kind != "retained", "provider_status": {
            "origin": "unavailable", "reason": HOSTILE, "unavailable": True},
        "uncertainty": [], "segment_ids": []} for kind in ("retained", "rejected", "uncertain", "unattributed")]
    audit = {"artifact_sha": "a" * 64, "audit_sha": "d" * 64, "registry_sha": "e" * 64,
             "inventory_report_sha": "f" * 64, "inventory_policy_hash": "1" * 64,
             "parser": "fixture", "parser_version": "1", "normalization_policy": "fixture",
             "entries": entries, "structural_gaps": [{"gap_id": "gap", "page_index": 0,
                 "literal_excerpt": HOSTILE, "reason": "unaligned table cell"}],
             "segments": [], "page_coverage": [], "counts": {"candidates": 4},
             "coverage_complete": False, "attribution_complete": False, "artifact_issues": []}
    return {"audit": audit, "snapshot": {"run_id": "run-1", "artifact": {"artifact_sha": "a" * 64},
        "assessment_generation": "2" * 64, "matrix_generation": "3" * 64, "rows": []}, "promotions": []}


def render(environment, value):
    return environment.get_template("inventory.html").render(request=None, vm=inventory_audit_view(value),
        csrf_token="fixture-csrf", request_id="fixture-request", runtime_unverified=True)


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.current = [], None
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""), "fields": {}}
            self.forms.append(self.current)
        if tag in {"input", "textarea"} and self.current is not None and "name" in attrs:
            self.current["fields"][attrs["name"]] = attrs.get("value", "")
    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def promotions(html):
    return [form for form in Forms(html).forms if form["action"].endswith("/inventory/promote")]


def test_hostile_quotes_reasons_and_all_dispositions_visible(environment):
    """what_bug_this_catches: omitted/uncertain context becomes XSS or disappears as an assumed non-requirement."""
    html = render(environment, payload())
    assert HOSTILE not in html and "&lt;script&gt;" in html and "<script>" not in html
    for name in ("retained", "rejected", "uncertain", "unattributed"):
        assert f'id="{name}"' in html
    assert "semantic recall is not assessed" in html
    assert "Provider unavailable or unattributed" in html
    assert "Unanchored structural gaps" in html


def test_promotion_is_closed_versioned_pending_only(environment):
    """what_bug_this_catches: a promotion sends actor/span/provider/approval or loses expected identities."""
    forms = promotions(render(environment, payload()))
    assert len(forms) == 3
    assert len({f["fields"]["request_id"] for f in forms}) == 3
    for form in forms:
        assert set(form["fields"]) == {"csrf_token", "request_id", "candidate_id", "expected_artifact_sha",
                                        "expected_assessment_generation", "expected_matrix_generation", "reason"}
        assert form["fields"]["candidate_id"] != "candidate-retained"
        assert form["fields"]["expected_artifact_sha"] == "a" * 64
        assert form["fields"]["expected_assessment_generation"] == "2" * 64
        assert form["fields"]["expected_matrix_generation"] == "3" * 64
    assert "Add pending requirement — no approval" in render(environment, payload())


@pytest.mark.parametrize("mode", ["historical", "wrong_artifact", "missing_matrix"])
def test_stale_missing_or_foreign_audit_disables_promotion(environment, mode):
    """what_bug_this_catches: stale/foreign source audit promotes into a different current matrix."""
    value = payload()
    if mode == "historical":
        value["snapshot"]["historical"] = True
    elif mode == "wrong_artifact":
        value["snapshot"]["artifact"]["artifact_sha"] = "9" * 64
    else:
        value["snapshot"].pop("matrix_generation")
    assert promotions(render(environment, value)) == []


def test_existing_human_correction_does_not_rewrite_audit_or_offer_duplicate(environment):
    """what_bug_this_catches: human promotion mutates machine truth, becomes blind evidence, or can duplicate."""
    value = payload()
    original = deepcopy(value["audit"])
    value["snapshot"]["rows"] = [{"human_origin": {"candidate_id": "candidate-rejected", "artifact_sha": "a" * 64}}]
    value["promotions"] = [{"candidate_id": "candidate-rejected", "row_id": "human-row", "actor": "fixture-human",
                            "reason": HOSTILE, "event_id": "fixture-event", "created_at": "fixture-time"}]
    html = render(environment, value)
    assert all(f["fields"]["candidate_id"] != "candidate-rejected" for f in promotions(html))
    assert value["audit"] == original
    assert "not a blind method prediction" in html
    assert "A separate human-added atom already exists" in html
