"""Later-session policy binding regression SOURCE ONLY; uncollected/unrun.

The future isolated fixture fakes distribution metadata, not predictions. It
creates a genuine NEW deterministic B0b evidence assessment over an empty
fictional corpus, using explicitly mocked inventory decisions; no SDK/client/
network/model judgment may run. This is no installation or
historical/live execution receipt and establishes no product compatibility.
"""
from argparse import Namespace
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from rfp_matrix.cli import _bind_review_policy, build_parser
from rfp_matrix.evidence import assess_requirement, inventory_report, make_corpus
from rfp_matrix.providers import ProviderConfig, review_policy_identity
from rfp_matrix.providers.live import TypeSafeProvider
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.review.application import ReviewerApplication
from rfp_matrix.review.graph import LangGraphReviewDriver
from rfp_matrix.review.policy_binding import ReviewPolicyBinding, ReviewPolicyBindings, bind_review_policies
from rfp_matrix.source import SourceRegistry, parse_text_pages, policy_context
from rfp_matrix.export import export_matrix


@pytest.fixture
def typed_runs(tmp_path, monkeypatch):
    import rfp_matrix.provenance as provenance
    actual_dependency = provenance._dependency
    monkeypatch.setattr(provenance, "_PINS", {})
    monkeypatch.setattr(provenance, "_RESTART", set())

    def synthetic_metadata(name):
        if name == "typesafe-sdk":
            return {"status": "available", "version": "0.7.5", "reason": None}
        return actual_dependency(name)

    monkeypatch.setattr(provenance, "_dependency", synthetic_metadata)
    monkeypatch.setattr(TypeSafeProvider, "judge", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("No judgment in deterministic fixture")))
    store = ReviewStore(tmp_path / "state")
    artifact = parse_text_pages(["The vendor must provide daily audit logs."], source_id="synthetic-policy-binding",
                               terms="Self-authored fictional development fixture")
    corpus = make_corpus([])
    class SyntheticInventory:
        method = "B0b"

        def judge(self, state, questions):
            return {"schema_version": 1, "status": "ok", "origin": "mock", "model": None, "sdk_version": None,
                "usage": None, "reason": "Self-authored synthetic inventory decisions, not model execution",
                "answers": {name: {"type": "noul", "noul": 1.0 if name in {"requirement", "mandatory", "atomic"} else 0.0}
                            for name in questions}}

    report = inventory_report(artifact, method="B0b", provider=SyntheticInventory())
    SourceRegistry(store.root / "sources").save(artifact)
    configs, paths = {}, {}
    for run_id in ("R", "OTHER"):
        current = policy_context("B0b")
        prediction = ProviderConfig.from_mapping({"method": "B0b", "enabled": True, "budget_usd": "0.01",
            "document_id": run_id, "source_sha256": artifact["artifact_sha"], "corpus_sha256": corpus["generation"],
            "semantic_fingerprint": current["fingerprint"], "model_provenance": "Synthetic metadata fixture; no model executed"})
        # Only explicit synthetic inventory mocks, never model execution. assess_requirement
        # performs B0b's genuine deterministic baseline evidence assessment and
        # captures its own new receipt. Nothing rewrites a historical answer.
        requirement = deepcopy(report["requirements"][0])
        assessment = assess_requirement(requirement, corpus, method="B0b", provider=TypeSafeProvider(prediction))
        assert assessment["origin"] == "baseline" and assessment["model"] is None
        assert assessment["review_policy_identity"]
        row = {"schema_version": 1, "row_id": requirement["requirement_id"], "requirement": requirement,
               "assessment": assessment, "answer_text": "No supporting evidence found in this fictional corpus.",
               "validation": assessment["validation"], "unvalidated_note": ""}
        store.create_run(run_id, artifact, corpus, [row], inventory_report=report)
        review = replace(prediction, enabled=False, budget_usd="0", admission_reference="", approved_config_sha256="")
        assert prediction.config_sha256 != review.config_sha256
        configs[run_id] = review
        paths[run_id] = tmp_path / (run_id + "-review.json")
        paths[run_id].write_text(json.dumps(asdict(review)), encoding="utf-8")
    return store, configs, paths


def test_reopened_review_decision_export_uses_read_identity_without_rebinding_old_config_sha(typed_runs, tmp_path):
    """what_bug_this_catches: later processes unable to review actual new typed-policy evidence without enabling an inference provider."""
    store, _, paths = typed_runs
    before = store.load_run("R")
    reopened = ReviewStore(store.root)
    assert reopened.policy_status("R")["policy_status"]["status"] == "missing"
    args = build_parser().parse_args(["review", "--run", "R", "--review-policy-config", str(paths["R"])])
    _bind_review_policy(args, reopened)
    selected = ReviewerApplication(reopened).get_row("R", before["rows"][0]["row_id"])
    assert selected["row"]["policy_status"]["status"] == "current"
    assert reopened.load_run("R") == before and reopened.events("R") == []
    row = before["rows"][0]
    event = reopened.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=before["assessment_generation"],
        expected_row_revision=row["decision_revision"], request_id="new-gap", reason="Acknowledge the actual empty fictional corpus only")
    after = reopened.load_run("R")
    assert after["rows"][0]["row_revision"] == row["row_revision"] and after["rows"][0]["decision_revision"] != row["decision_revision"]
    assert after["rows"][0]["assessment"] == row["assessment"]
    receipt = export_matrix(reopened, "R", tmp_path / "exports", request_id="new-export", expected_matrix_generation=after["matrix_generation"])
    assert receipt["policy_current_at_return"] is True and receipt["current_at_return"] is True
    with pytest.raises(ReviewError) as error:
        reopened.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=after["assessment_generation"],
            expected_row_revision=row["decision_revision"], request_id="old-row-cas", reason="Stale CAS must still fail")
    assert error.value.code == "stale_revision" and reopened.events("R") == [event]


@pytest.mark.parametrize("change", ["run", "source", "corpus", "method", "model"])
def test_wrong_run_generation_method_or_model_cannot_become_current(typed_runs, change):
    """what_bug_this_catches: a valid inert handle for different pins being silently reused as this run's current policy."""
    store, configs, _ = typed_runs
    original = store.load_run("R")
    changes = {"run": {"document_id": "OUTSIDE"}, "source": {"source_sha256": "0" * 64},
               "corpus": {"corpus_sha256": "0" * 64}, "method": {"method": "F1"}, "model": {"model": "jev-1.13.1"}}
    config = replace(configs["R"], **changes[change])
    if change == "model":
        bind_review_policies(store, [config], run_ids=["R"])
        status = store.policy_status("R")["policy_status"]
        assert status["status"] == "mismatch" and status["allows_current_claim"] is False
    else:
        with pytest.raises(ReviewError) as error:
            bind_review_policies(store, [config], run_ids=["R"])
        assert error.value.code == "review_policy_scope_mismatch"
    assert store.load_run("R") == original and store.events("R") == []


@pytest.mark.parametrize("contents", [None, "[]", '{"enabled":false,"enabled":false}', '{"budget_usd":NaN}', '{"credential":"FAKE-NOT-A-KEY"}'])
def test_missing_or_malformed_file_never_collects_or_constructs_inference(typed_runs, tmp_path, monkeypatch, contents):
    """what_bug_this_catches: invalid policy input triggering source collection, a provider factory, tape, ledger or secret fallback."""
    import rfp_matrix.providers as providers
    import rfp_matrix.source as source
    store, _, _ = typed_runs

    def forbidden(*a, **kw):
        raise AssertionError("No fallback dispatch/collection belongs to malformed policy")

    for name in ("create_provider", "CallLedger", "load_replay_tape"):
        monkeypatch.setattr(providers, name, forbidden)
    monkeypatch.setattr(source, "policy_context", forbidden)
    path = tmp_path / "malformed-review.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    before = store.load_run("R")
    with pytest.raises(ValueError):
        _bind_review_policy(Namespace(command="review", run="R", review_policy_config=[str(path)]), store)
    assert store.policy_provider is None and store.load_run("R") == before


def test_binding_constructor_is_inert_and_rejects_a_native_prediction_port(typed_runs, monkeypatch):
    """what_bug_this_catches: constructor source/SDK access or a JudgmentPort being smuggled into the read-only policy binding."""
    import rfp_matrix.source as source
    import rfp_matrix.provenance as provenance
    store, configs, _ = typed_runs
    config = configs["R"]
    handle = review_policy_identity(config, semantic_provenance=policy_context("B0b")["descriptor"])
    assert handle.status == "available"

    def forbidden(*a, **kw):
        raise AssertionError("Constructors must not collect current metadata")

    monkeypatch.setattr(source, "policy_context", forbidden)
    monkeypatch.setattr(provenance, "semantic_policy_fingerprint", forbidden)
    bindings = ReviewPolicyBindings([ReviewPolicyBinding(config, handle)])
    assert bindings.resolve(store.load_run("R"), "B0b") is handle
    assert not hasattr(handle, "judge") and not hasattr(bindings, "judge")
    with pytest.raises(ReviewError) as error:
        ReviewPolicyBindings([ReviewPolicyBinding(config, TypeSafeProvider(config))])
    assert error.value.code == "review_policy_identity_required"


def test_multi_run_serve_bindings_are_isolated_and_invalid_batch_never_partially_applies(typed_runs):
    """what_bug_this_catches: serving a second run with the first run's policy or retaining a partial binding after a bad later file."""
    store, configs, paths = typed_runs
    args = build_parser().parse_args(["serve", "--run", "R", "--run", "OTHER", "--port", "20042",
        "--port-claim-reference", "SYNTHETIC-PARSER-REFERENCE-NO-LISTENER", "--private-login-delivery",
        "--review-policy-config", str(paths["R"])])
    _bind_review_policy(args, store)  # No serve_local/session/ACL/listener call.
    app = ReviewerApplication(store)
    assert app.get_run("R")["policy_status"]["allows_current_claim"] is True
    assert app.get_run("OTHER")["policy_status"]["status"] == "missing"
    bindings = bind_review_policies(store, [configs["R"], configs["OTHER"]], run_ids=["R", "OTHER"])
    assert app.get_run("OTHER")["policy_status"]["allows_current_claim"] is True
    with pytest.raises(ReviewError):
        bind_review_policies(store, [configs["R"], replace(configs["OTHER"], corpus_sha256="0" * 64)], run_ids=["R", "OTHER"])
    assert store.policy_provider is bindings
    with pytest.raises(ReviewError) as error:
        bind_review_policies(store, [configs["R"], configs["R"]], run_ids=["R", "OTHER"])
    assert error.value.code == "duplicate_review_policy" and store.policy_provider is bindings
    changed = store.load_run("R")
    changed["corpus"]["generation"] = "0" * 64
    with pytest.raises(ReviewError) as error:
        bindings.resolve(changed, "B0b")
    assert error.value.code == "review_policy_scope_mismatch"
    assert bindings.resolve(store.load_run("R"), "B0a") is None


@pytest.mark.parametrize("failure", ["model", "missing", "restart"])
def test_changed_model_blocks_old_approval_recovery_and_legacy_receipt_absence_never_repairs(typed_runs, monkeypatch, failure):
    """what_bug_this_catches: new review config relabelling old approval/receipt history as fresh evidence or reopening a graph before policy checks."""
    store, configs, _ = typed_runs
    bind_review_policies(store, [configs["R"]], run_ids=["R"])
    before = store.load_run("R")
    row = before["rows"][0]
    event = store.decide("R", row["row_id"], "accept_gap", expected_assessment_generation=before["assessment_generation"],
        expected_row_revision=row["decision_revision"], request_id="before-model-change", reason="Original fictional corpus absence")
    after = store.load_run("R")
    with monkeypatch.context() as patch:
        if failure == "model":
            bind_review_policies(store, [replace(configs["R"], model="jev-1.13.1")], run_ids=["R"])
        elif failure == "missing":
            store.policy_provider = None
        else:
            import rfp_matrix.source as source
            actual_context = source.policy_context

            def drifted(*args, **kwargs):
                return {**actual_context(*args, **kwargs), "status": "restart_required"}

            patch.setattr(source, "policy_context", drifted)
        driver = LangGraphReviewDriver(store.root / "checkpoints.sqlite3")
        patch.setattr(driver, "_graph", lambda *a: (_ for _ in ()).throw(AssertionError("No checkpoint before policy guard")))
        with pytest.raises(ReviewError) as error:
            driver.apply(store, "R", event)
        assert error.value.code == "policy_not_current" and store.pending_intents("R")
        assert store.load_run("R") == after
    # Explicit synthetic legacy fixture: removing a receipt is not a product
    # migration or proof repair. No loader is permitted to add it back.
    legacy = deepcopy(row)
    legacy["assessment"].pop("review_policy_identity")
    store.create_run("LEGACY", before["artifact"], before["corpus"], [legacy], inventory_report=before["inventory_report"])
    legacy_config = replace(configs["R"], document_id="LEGACY")
    baseline = store.load_run("LEGACY")
    bind_review_policies(store, [legacy_config], run_ids=["LEGACY"])
    assert store.policy_status("LEGACY")["policy_status"]["status"] == "legacy"
    assert store.load_run("LEGACY") == baseline and "review_policy_identity" not in baseline["rows"][0]["assessment"]


def test_exact_cli_scope_tui_flags_and_no_live_admission_mixing(typed_runs, monkeypatch):
    """what_bug_this_catches: flags missing on later-session commands or review identity accidentally reaching inference admission."""
    import rfp_matrix.cli as cli
    store, _, paths = typed_runs
    parser = build_parser()
    commands = [
        ["review", "--run", "R", "--interactive"], ["resume", "--run", "R"], ["policy-status", "--run", "R"],
        ["decide", "--run", "R", "--row", "row", "--action", "defer", "--assessment-generation", "1" * 64, "--row-revision", "2" * 64, "--request-id", "D1"],
        ["export", "--run", "R", "--out", "exports", "--matrix-generation", "1" * 64, "--request-id", "X1"],
        ["serve", "--run", "R", "--port", "20042", "--port-claim-reference", "SYNTHETIC-NO-LISTENER", "--private-login-delivery"],
    ]
    for argv in commands:
        assert parser.parse_args([*argv, "--review-policy-config", str(paths["R"])]).review_policy_config == [str(paths["R"])]
    monkeypatch.setattr(cli, "_policy_mapping", lambda *a: (_ for _ in ()).throw(AssertionError("Must reject mixing before file read")))
    with pytest.raises(ReviewError) as error:
        _bind_review_policy(Namespace(command="review", run="R", review_policy_config=[str(paths["R"])], allow_live=True), store)
    assert error.value.code == "review_policy_live_mixing" and store.policy_provider is None
