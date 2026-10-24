"""P5.15 regression SOURCE, UNRUN in Dirty Mode.

All descriptors, observations and hashes below are authored fixtures, including
the literal origin 'live'. None records real execution, SDK availability or
provider admission. No collector or optional SDK is needed by these definitions.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rfp_matrix import provenance
from rfp_matrix.contracts import M3Error, canonical_json, digest, validate_record
from rfp_matrix.evidence.consumer import (
    assessment_generation, assessment_policy, assessment_policy_status,
    assessment_review_identity, call_judgment, judgment_port,
)
from rfp_matrix.providers import ProviderConfig, REVIEW_POLICY_PURPOSE, review_policy_identity
from rfp_matrix.source.policy import _context
from rfp_matrix.web.presentation import row_view


def fixture_context(method="F1", *, seed="a", status="available", sdk_absent=False):
    groups = ("parse", "segment", "retrieve", "validate") + (() if method == "B0a" else ("judge",))
    components = {}
    for group in groups:
        dependencies = {}
        libraries = () if method == "F2" and group == "judge" else provenance.LIBRARIES[group]
        for name in libraries:
            dependencies[name] = ({"status": "unavailable", "version": None, "reason": "distribution_not_installed"}
                if name == "typesafe-sdk" and sdk_absent else
                {"status": "available", "version": "0.7.5" if name == "typesafe-sdk" else "authored-fixture", "reason": None})
        body = {"status": "available", "files": [
            {"path": name, "kind": "python_ast" if name.endswith(".py") else "text_lf",
             "status": "available", "sha256": seed * 64, "reason": None}
            for name in sorted(provenance.GROUPS[group])], "dependencies": dependencies}
        components[group] = {**body, "fingerprint": digest("m3.source-component.v1", body)}
    identity = {"method": method, "parser_profile": None, "components": components,
        "runtime": {"python_implementation": "CPython", "python_version": "3.13.5",
                    "unicode_version": "authored-fixture", "platform": "fixture", "byteorder": "little",
                    "canonical_policy": provenance.CANONICAL_POLICY}}
    descriptor = provenance._descriptor(provenance.SEMANTIC_PROFILE, identity, status=status,
        reasons=["authored-process-drift"] if status == "restart_required" else [])
    return _context(method, None, descriptor)


@pytest.fixture(autouse=True)
def forbid_collection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure review fixtures must not collect provenance")
    monkeypatch.setattr(provenance, "semantic_policy_fingerprint", forbidden)


def authored_assessment(method="F1", origin="live"):
    context = fixture_context(method)
    config = ProviderConfig(method=method, enabled=True, budget_usd="1", document_id="authored-run",
        model="gpt-fixture-2026-10-01" if method == "F2" else "jev-1.13.0",
        sdk_name="python-stdlib-urllib" if method == "F2" else "typesafe-sdk",
        sdk_version="3.13.5" if method == "F2" else "0.7.5", source_sha256="b" * 64,
        corpus_sha256="c" * 64, semantic_fingerprint=context["fingerprint"])
    port = SimpleNamespace(config=config)  # Identity only, no judge/transport.
    baseline = method == "B0b"
    reported = None if baseline or origin in {"mock", "unavailable"} else config.model
    raw = {"origin": origin, "model": reported, "sdk_name": config.sdk_name,
           "sdk_version": config.sdk_version, "semantic_fingerprint": context["fingerprint"]}
    if method == "F2":
        raw.update(status="needs_review", answers={}, raw_classifications={"relation": "unknown"},
                   distribution_semantics="generated-closed-labels-no-probabilities")
    assessment = validate_record("assessment", {
        "schema_version": 1, "requirement_id": "R1", "requirement_revision": "d" * 64,
        "corpus_generation": "c" * 64, "status": "needs_review", "support": [], "counterevidence": [],
        "candidates": [], "complete": False, "reason": "Authored observation, not execution",
        "method": method, "origin": "baseline" if baseline else origin, "model": reported,
        "policy_hash": assessment_policy(method, port, context), "policy_provenance": context,
        "judgments": [] if baseline else [{"record_id": "fictional", "outcome": raw}], "usage": None,
        "validation": {"schema_version": 1, "state": "pending", "method": "restricted_template",
                       "provenance": "baseline" if baseline else origin, "reason": "Authored fixture only"}})
    assessment["review_policy_identity"] = assessment_review_identity(assessment, port)
    readonly = replace(config, enabled=False, budget_usd="0", admission_reference="", approved_config_sha256="")
    handle = review_policy_identity(readonly, semantic_provenance=context["descriptor"])
    return assessment, context, config, handle


def test_later_session_readonly_identity_matches_without_execution_or_hash_rebind():
    """what_bug_this_catches: disabled review config is compared to admission configSHA or rewrites old proof."""
    assessment, context, execution_config, handle = authored_assessment()
    before = deepcopy(assessment)
    assert execution_config.config_sha256 != handle.config.config_sha256
    assert handle.status == "available" and not hasattr(handle, "judge")
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] == "current" and result["allows_current_claim"] is True
    assert result["policy_input"] == REVIEW_POLICY_PURPOSE and result["execution_verified"] is False
    assert assessment == before and assessment["origin"] == "live"


def test_missing_input_and_legacy_receipt_stay_blocked_and_byte_preserved():
    """what_bug_this_catches: old answers silently supply current policy or defaults change legacy assessment bytes."""
    assessment, context, _, handle = authored_assessment()
    missing = assessment_policy_status(assessment, current_context=context)
    assert missing["status"] == "missing" and missing["action"] == "supply_review_policy_config"
    assessment.pop("review_policy_identity")
    before = canonical_json(assessment)
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] == "legacy" and not result["allows_current_claim"]
    assert canonical_json(validate_record("assessment", assessment)) == before
    assert "review_policy_identity" not in assessment


@pytest.mark.parametrize("field,value", [("model", "jev-1.14.0"), ("document_id", "another-run"),
    ("source_sha256", "e" * 64), ("corpus_sha256", "f" * 64)])
def test_exact_run_source_corpus_and_model_mismatch_fail_closed(field, value):
    """what_bug_this_catches: a correct semantic hash alone authorizes another model or source scope."""
    assessment, context, _, handle = authored_assessment()
    other = review_policy_identity(replace(handle.config, **{field: value}), semantic_provenance=context["descriptor"])
    result = assessment_policy_status(assessment, current_context=context, provider=other)
    assert result["status"] == "mismatch" and result["allows_current_claim"] is False


@pytest.mark.parametrize("origin", ["mock", "replay", "unavailable"])
def test_readonly_live_identity_cannot_launder_prediction_origin(origin):
    """what_bug_this_catches: a live-looking policy turns simulated/replayed/unavailable evidence into live current proof."""
    assessment, context, _, handle = authored_assessment(origin=origin)
    original = deepcopy(assessment)
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] == "mismatch" and not result["allows_current_claim"]
    assert assessment == original and assessment["origin"] == origin


@pytest.mark.parametrize("kind", ["assessment", "receipt", "old_hash", "raw_sdk", "raw_model", "semantic_rebind"])
def test_tampered_receipt_or_observations_never_establish_freshness(kind):
    """what_bug_this_catches: rehashing captured evidence or overwriting provenance bypasses genuine reassessment."""
    assessment, context, config, handle = authored_assessment()
    if kind == "assessment":
        assessment["reason"] = "changed content"
    elif kind == "receipt":
        assessment["review_policy_identity"]["prediction_origin"] = "mock"
    elif kind == "old_hash":
        assessment["policy_hash"] = "0" * 64
        assessment["review_policy_identity"] = assessment_review_identity(assessment, SimpleNamespace(config=config))
    elif kind in {"raw_sdk", "raw_model"}:
        assessment["judgments"][0]["outcome"]["sdk_version" if kind == "raw_sdk" else "model"] = "wrong"
        assessment["review_policy_identity"] = assessment_review_identity(assessment, SimpleNamespace(config=config))
    else:
        context = fixture_context(seed="e")
        assessment["policy_provenance"] = context
        handle = review_policy_identity(replace(handle.config, semantic_fingerprint=context["fingerprint"]),
                                        semantic_provenance=context["descriptor"])
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] == "mismatch" and not result["allows_current_claim"]


@pytest.mark.parametrize("kind", ["sdk", "questions", "restart", "missing_descriptor", "wrong_method"])
def test_actual_current_metadata_and_restart_guards_are_required(kind):
    """what_bug_this_catches: current source hash alone bypasses missing SDK/policy/method/process metadata."""
    assessment, context, _, handle = authored_assessment()
    config = handle.config
    if kind == "sdk":
        context = fixture_context(sdk_absent=True)
        config = replace(config, semantic_fingerprint=context["fingerprint"])
    elif kind == "questions":
        config = replace(config, question_policy="unknown-question-policy")
    elif kind == "restart":
        context = fixture_context(status="restart_required")
    elif kind == "wrong_method":
        context = fixture_context("B0b")
    descriptor = None if kind == "missing_descriptor" else context["descriptor"]
    handle = review_policy_identity(config, semantic_provenance=descriptor)
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] != "current" and not result["allows_current_claim"]
    if kind == "restart":
        assert result["status"] == "restart_required" and result["action"] == "restart_then_reassess"


def test_exact_readonly_handle_is_rejected_as_prediction_or_generation_port():
    """what_bug_this_catches: the identity port becomes a provider or can create/rebind assessment receipts."""
    assessment, context, _, handle = authored_assessment()
    for operation in (lambda: judgment_port(handle, "F1"),
                      lambda: assessment_generation("b" * 64, "c" * 64, "F1", handle, context),
                      lambda: assessment_review_identity(assessment, handle)):
        with pytest.raises(M3Error) as caught:
            operation()
        assert caught.value.code == "review_identity_not_predictor"
    result = call_judgment(handle, {}, {}, method="F1")
    assert result["status"] == "unavailable" and result["raw"]["code"] == "review_identity_not_predictor"


def test_duck_marker_is_not_a_trusted_readonly_handle():
    """what_bug_this_catches: caller-supplied purpose strings or marker booleans gain trusted currentness."""
    assessment, context, _, _ = authored_assessment()
    fake = SimpleNamespace(read_only_policy=True, purpose=REVIEW_POLICY_PURPOSE, status="available")
    result = assessment_policy_status(assessment, current_context=context, provider=fake)
    assert result["status"] == "mismatch" and not result["allows_current_claim"]


def test_b0b_baseline_origin_stays_baseline_and_b0a_needs_no_optional_sdk():
    """what_bug_this_catches: readonly identity relabels B0b rules as model evidence or makes B0a need TypeSafe."""
    assessment, context, _, handle = authored_assessment("B0b")
    assert assessment_policy_status(assessment, current_context=context, provider=handle)["status"] == "current"
    assert assessment["origin"] == "baseline" and assessment["model"] is None
    baseline = fixture_context("B0a", sdk_absent=True)
    value = {"method": "B0a", "policy_provenance": baseline,
             "policy_hash": assessment_policy("B0a", judgment_port(None, "B0a"), baseline)}
    assert assessment_policy_status(value, current_context=baseline)["status"] == "current"
    assert all("typesafe-sdk" not in item["dependencies"] for item in baseline["descriptor"]["identity"]["components"].values())


@pytest.mark.parametrize("state", ["needs_review", "cannot_answer_conflicting"])
def test_f2_readonly_identity_never_changes_uncalibrated_state(state):
    """what_bug_this_catches: later-session policy matching upgrades generated closed labels into supported evidence."""
    assessment, context, config, handle = authored_assessment("F2")
    assessment["status"] = state
    assessment["review_policy_identity"] = assessment_review_identity(assessment, SimpleNamespace(config=config))
    before = deepcopy(assessment)
    assert assessment_policy_status(assessment, current_context=context, provider=handle)["status"] == "current"
    assert assessment == before and assessment["complete"] is False
    assessment["status"], assessment["complete"] = "supported", True
    assessment["review_policy_identity"] = assessment_review_identity(assessment, SimpleNamespace(config=config))
    assert assessment_policy_status(assessment, current_context=context, provider=handle)["status"] == "mismatch"


def test_resealed_f2_probability_wire_is_still_not_current_closed_labels():
    """what_bug_this_catches: a structurally valid receipt hides relabeled F2 probability claims."""
    assessment, context, config, handle = authored_assessment("F2")
    assessment["judgments"][0]["outcome"].update(answers={"relation": {"confidence": 1.0}},
        distribution_semantics="invented-probabilities")
    assessment["review_policy_identity"] = assessment_review_identity(assessment, SimpleNamespace(config=config))
    result = assessment_policy_status(assessment, current_context=context, provider=handle)
    assert result["status"] == "mismatch" and not result["allows_current_claim"]


def test_vm_warning_is_actionable_plain_and_cannot_override_opaque_cas_or_human_lineage():
    """what_bug_this_catches: missing typed policy enables controls or raw config/actor leaks through policy rendering."""
    snapshot = {"run_id": "authored-run", "matrix_generation": "a" * 64, "assessment_generation": "b" * 64}
    row = {"row_id": "R1", "row_revision": "c" * 64, "decision_revision": "decision-event:17",
        "assessment_generation": "b" * 64, "approval": "approved_positive", "answer_text": "old approved text",
        "requirement": {"mandatory": True, "applicability": "applies", "uncertainty": [], "anchors": []},
        "assessment": {"method": "F1", "status": "supported", "complete": True},
        "human_origin": {"candidate_id": "authored-human-proposal", "blind_method_prediction": False},
        "policy_status": {"status": "missing", "allows_current_claim": False, "reasons": [],
            "action": "supply_review_policy_config", "config": {"secret": "DO-NOT-COPY"}, "actor": "DO-NOT-COPY"}}
    before = deepcopy(row)
    view = row_view(snapshot, row)
    assert view["controls"]["expected_row_revision"] == "decision-event:17"
    assert view["controls"]["actions"] == [] and view["row"]["approved_answer"] is None
    assert "--review-policy-config FILE" in " ".join(view["policy_status"]["reasons"])
    assert "DO-NOT-COPY" not in canonical_json(view["policy_status"])
    assert row == before
