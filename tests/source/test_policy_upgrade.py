"""Policy upgrade regression SOURCE; UNRUN in Dirty Mode.

Descriptors below contain explicitly fabricated fixture component hashes and
metadata. They test descriptor wiring/integrity only, never actual disk/process
fingerprints, library admission, trusted attestation or measured execution.
"""
from copy import deepcopy

import pytest

from rfp_matrix import provenance
from rfp_matrix.contracts import M3Error, canonical_json, digest, validate_record
from rfp_matrix.evidence import (
    assessment_policy_status, build_matrix, inventory_audit, make_corpus,
    promote_inventory_candidate, reassess_rows,
)
from rfp_matrix.evidence.consumer import assessment_generation
from rfp_matrix.source import (
    artifact_policy_status, make_anchor, parse_text_pages, policy_status, resolve_anchor,
)
from rfp_matrix.source.policy import _context
from rfp_matrix.web.presentation import row_view


def fixture_descriptor(method="B0a", parser_profile=None, *, seed="a", status="available", sdk_absent=True):
    selected = ("parse", "segment", "retrieve", "validate") + (() if method == "B0a" else ("judge",))
    components = {}
    for group in selected:
        files = [{"path": name, "kind": "python_ast" if name.endswith(".py") else "text_lf",
                  "status": "available", "sha256": seed * 64, "reason": None} for name in sorted(provenance.GROUPS[group])]
        libraries = () if group == "judge" and method == "F2" else provenance.LIBRARIES[group]
        dependencies = {name: ({"status": "unavailable", "version": None, "reason": "distribution_not_installed"}
            if name == "typesafe-sdk" and sdk_absent else {"status": "available", "version": "authored-fixture-only", "reason": None})
            for name in libraries}
        body = {"status": "available", "files": files, "dependencies": dependencies}
        components[group] = {**body, "fingerprint": digest("m3.source-component.v1", body)}
    identity = {"method": method, "parser_profile": parser_profile, "components": components,
        "runtime": {"python_implementation": "CPython", "python_version": "authored-fixture", "unicode_version": "authored-fixture",
                    "platform": "fixture", "byteorder": "little", "canonical_policy": provenance.CANONICAL_POLICY}}
    return provenance._descriptor(provenance.SEMANTIC_PROFILE, identity, status=status,
        reasons=["fixture-process-source-drift"] if status == "restart_required" else [])


def context(method="B0a", parser_profile=None, **kwargs):
    return _context(method, parser_profile, fixture_descriptor(method, parser_profile, **kwargs))


@pytest.fixture
def fake_policy_ports(monkeypatch):
    """Future execution uses pure authored descriptor ports; never real collection."""
    def captured(method, parser_profile=None):
        return context(method, parser_profile)
    monkeypatch.setattr("rfp_matrix.source.artifacts.policy_context", captured)
    monkeypatch.setattr("rfp_matrix.evidence.engine.policy_context", captured)
    return captured


def matrix_fixture():
    artifact = parse_text_pages(["General background for discussion.\n\nThe vendor must provide encrypted backup."],
        source_id="authored-policy-upgrade", terms="Authored development only")
    corpus = make_corpus([{"record_id": "fictional", "text": "We provide encrypted backup.",
                           "terms": "Project-authored fictional vendor", "fictional": True}])
    return artifact, corpus, build_matrix(artifact, corpus)


def test_canonical_python_identity_ignores_line_endings_comments_docstrings():
    """what_bug_this_catches: CRLF/format-only copies invalidate semantic identity while real code changes do not."""
    lf = b'def judge(value):\n    """commentary"""\n    return value + 1\n'
    crlf = b'def judge(value):\r\n    # ignored comment\r\n    return value + 1\r\n'
    assert provenance.canonical_source(lf, python=True) == provenance.canonical_source(crlf, python=True)
    changed = b'def judge(value):\n    return value + 2\n'
    assert provenance.canonical_source(lf, python=True) != provenance.canonical_source(changed, python=True)


def test_identical_descriptors_stable_and_baseline_has_no_sdk_requirement():
    """what_bug_this_catches: missing TypeSafe SDK breaks keyless B0a or identical identities drift spuriously."""
    first, second = context(), context()
    assert first == second and policy_status(first, second)["status"] == "current"
    assert all("typesafe-sdk" not in component["dependencies"] for component in first["descriptor"]["identity"]["components"].values())
    assert first["execution_verified"] is False and first["descriptor"]["attestation"] is False


@pytest.mark.parametrize("kind", ["code", "parser", "method", "restart"])
def test_code_parser_method_and_process_drift_cannot_keep_old_policy_current(kind):
    """what_bug_this_catches: changed code/parser/method or an old loaded process retains current approval eligibility."""
    old = context()
    new = context(seed="b") if kind == "code" else context(parser_profile={"parser": "synthetic-text-pages", "parser_version": "2"}) if kind == "parser" else context("F1") if kind == "method" else context(status="restart_required")
    result = policy_status(old, new)
    assert result["status"] == ("restart_required" if kind == "restart" else "mismatch")
    assert result["allows_current_claim"] is False and result["execution_verified"] is False


def test_optional_provenance_absence_preserves_legacy_bytes_and_literal_profiles(fake_policy_ports):
    """what_bug_this_catches: schema defaults or current normalization rewrite immutable historical artifact/assessment hashes."""
    artifact, _, matrix = matrix_fixture()
    legacy = deepcopy(artifact)
    legacy.pop("policy_provenance")
    legacy["normalization_policy"] = "historical-profile-without-current-normalizer"
    legacy.pop("artifact_sha")
    legacy["artifact_sha"] = digest("m3.document-artifact.v1", legacy)
    encoded = canonical_json(legacy)
    validated = validate_record("artifact", legacy)
    assert "policy_provenance" not in validated and canonical_json(validated) == encoded
    text = legacy["pages"][0]["text"]
    pointer = make_anchor(legacy, 0, 0, len(text))
    assert resolve_anchor(legacy, pointer)["quote"] == text
    assert artifact_policy_status(legacy, current_context=context())["status"] == "legacy"
    assessment = deepcopy(matrix["rows"][0]["assessment"])
    assessment.pop("policy_provenance")
    assert canonical_json(validate_record("assessment", assessment)) == canonical_json(assessment)


def test_missing_or_relabelled_fingerprint_never_supplies_current_proof(fake_policy_ports):
    """what_bug_this_catches: arbitrary fingerprint/new provenance label is accepted without an actual new assessment."""
    _, _, matrix = matrix_fixture()
    assessment = matrix["rows"][0]["assessment"]
    original = deepcopy(assessment)
    assert assessment_policy_status(assessment, current_context=context())["status"] == "current"
    changed = context(seed="b")
    assert assessment_policy_status(assessment, current_context=changed)["status"] == "mismatch"
    rebound = deepcopy(assessment)
    rebound["policy_provenance"] = changed  # Hash/actual assessment not regenerated.
    assert assessment_policy_status(rebound, current_context=changed)["status"] == "mismatch"
    missing = deepcopy(context())
    missing["fingerprint"] = None
    assert policy_status(assessment["policy_provenance"], missing)["allows_current_claim"] is False
    assert assessment == original


def test_model_identity_changes_generation_without_calling_a_provider():
    """what_bug_this_catches: requested model changes reuse an old assessment generation/cache identity."""
    class ProviderIdentityOnly:
        method = "F1"
        def __init__(self, model):
            self.requested_model = model
        def judge(self, *args, **kwargs):
            raise AssertionError("Identity comparison must not dispatch")
    semantic = context("F1")
    old = assessment_generation("a" * 64, "b" * 64, "F1", ProviderIdentityOnly("jev-1.13.0"), semantic)
    new = assessment_generation("a" * 64, "b" * 64, "F1", ProviderIdentityOnly("jev-1.14.0"), semantic)
    assert old != new


def test_process_guard_state_is_separate_from_stable_semantic_identity():
    """what_bug_this_catches: restart reasons/status change code policy hashes or a stable hash bypasses restart."""
    old, restart = context(), context(status="restart_required")
    assert old["fingerprint"] == restart["fingerprint"] and old["context_sha"] != restart["context_sha"]
    from rfp_matrix.providers import DisabledProvider
    port = DisabledProvider("B0a")
    assert assessment_generation("a" * 64, "b" * 64, "B0a", port, old) == assessment_generation("a" * 64, "b" * 64, "B0a", port, restart)
    assert policy_status(old, restart)["allows_current_claim"] is False


def test_manual_origin_survives_real_reassessment_path_not_hash_rebinding(fake_policy_ports, monkeypatch):
    """what_bug_this_catches: policy upgrade loses human correction provenance or rebrands corrections as blind predictions."""
    artifact, corpus, matrix = matrix_fixture()
    audit = inventory_audit(artifact, matrix["inventory_report"])
    candidate = next(item["candidate_id"] for item in audit["entries"] if item["promotable"])
    row = promote_inventory_candidate(artifact, corpus, matrix["inventory_report"], candidate_id=candidate,
        assessment_generation=matrix["rows"][0]["assessment_generation"], policy={"schema_version": 1}, current_rows=matrix["rows"])
    old = deepcopy(row)
    monkeypatch.setattr("rfp_matrix.evidence.engine.policy_context", lambda method, parser_profile=None: context(method, parser_profile, seed="b"))
    refreshed = reassess_rows(artifact, corpus, [row])
    assert refreshed[0]["human_origin"] == old["human_origin"] and refreshed[0]["human_origin"]["blind_method_prediction"] is False
    assert refreshed[0]["assessment"]["policy_provenance"]["fingerprint"] == context(seed="b")["fingerprint"]
    assert refreshed[0]["approval"] == "pending" and row == old


def test_policy_guard_preserves_content_revision_and_opaque_decision_cas(fake_policy_ports):
    """what_bug_this_catches: policy metadata rewrites row content or derives a decision CAS from a content hash."""
    artifact, corpus, matrix = matrix_fixture()
    row = matrix["rows"][0]
    row["decision_revision"] = "opaque-decision-after-reject"
    snapshot = {"run_id": "fixture", "artifact": artifact, "corpus": corpus, "rows": [row],
        "assessment_generation": row["assessment_generation"], "matrix_generation": "a" * 64,
        "policy_status": assessment_policy_status(row["assessment"], current_context=context())}
    original = deepcopy(row)
    view = row_view(snapshot, row)
    assert view["row"]["row_revision"] == row["row_revision"]
    assert view["controls"]["expected_row_revision"] == "opaque-decision-after-reject" and row == original
    snapshot.pop("policy_status")
    unavailable = row_view(snapshot, row)
    assert unavailable["row"]["approved_answer"] is None and unavailable["controls"]["actions"] == []
    assert unavailable["policy_status"]["status"] == "missing"


def test_changed_parser_profile_rejects_current_claim_but_keeps_original_quotes(fake_policy_ports):
    """what_bug_this_catches: parser/profile metadata is silently rebound while old source anchors become unreadable."""
    artifact, _, _ = matrix_fixture()
    recorded = artifact["policy_provenance"]["semantic"]
    profile = {**recorded["parser_profile"], "parser_version": "999"}
    result = artifact_policy_status(artifact, current_context=context(parser_profile=profile))
    assert result["status"] == "mismatch" and result["allows_current_claim"] is False
    text = artifact["pages"][0]["text"]
    assert resolve_anchor(artifact, make_anchor(artifact, 0, 0, len(text)))["quote"] == text
