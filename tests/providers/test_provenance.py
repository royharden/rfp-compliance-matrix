"""SOURCE ONLY, UNRUN. Synthetic identities; no installed metadata or native collector."""
import copy

import pytest

from rfp_matrix import provenance as p
from rfp_matrix.contracts import digest, M3Error
from rfp_matrix.providers import ProviderConfig, create_provider, build_questions
from rfp_matrix.providers.ports import MockProvider, ReplayProvider
from rfp_matrix.providers.tape import replay_identity, make_replay_record, FileReplayProvider



def change_component(collection, group, *, missing=False):
    component = collection["identity"]["components"][group]
    component["files"][0].update(status="unavailable" if missing else "available",
        sha256=None if missing else "b" * 64, reason="source_unavailable" if missing else None)
    component["status"] = "unavailable" if missing else "available"
    body = {k: v for k, v in component.items() if k != "fingerprint"}
    component["fingerprint"] = None if missing else digest("m3.source-component.v1", body)


def test_public_binding_and_currentness_sources_are_semantic():
    """what_bug_this_catches: public rebinding or currentness code escapes invalidation."""
    assert "source/__init__.py" in p.GROUPS["parse"]
    assert {"source/__init__.py", "evidence/__init__.py", "review/currentness.py",
            "review/reassessment.py", "review/inventory.py", "review/policy_binding.py",
            "providers/review_identity.py"} <= set(p.GROUPS["validate"])


def test_python_canonicalization_ignores_format_docs_but_tracks_code():
    """what_bug_this_catches: CRLF/comments change identity or executable changes do not."""
    first = b'"doc"\r\n# comment\r\ndef value():\r\n    "doc"\r\n    return 1\r\n'
    second = b'def value():\n    return 1\n'
    assert p.canonical_source(first, python=True) == p.canonical_source(second, python=True)
    assert p.canonical_source(second, python=True) != p.canonical_source(second.replace(b"1", b"2"), python=True)
    assert p.canonical_source(b"a\r\nb\r", python=False) == b"a\nb\n"


def test_baseline_and_f2_do_not_require_optional_sdk(collection):
    """what_bug_this_catches: absent TypeSafe SDK blocks keyless or unrelated F2 policy."""
    baseline = p.semantic_policy_fingerprint("B0a")
    f2 = p.semantic_policy_fingerprint("F2")
    assert baseline["status"] == f2["status"] == "available"
    assert "judge" not in baseline["identity"]["components"]
    assert f2["identity"]["components"]["judge"]["dependencies"] == {}
    assert p.verify_fingerprint_descriptor(baseline) and p.verify_fingerprint_descriptor(f2)
    judge = collection["identity"]["components"]["judge"]
    judge["dependencies"]["typesafe-sdk"] = {"status": "available", "version": "0.7.5", "reason": None}
    body = {k: v for k, v in judge.items() if k != "fingerprint"}
    judge["fingerprint"] = digest("m3.source-component.v1", body)
    assert p.semantic_policy_fingerprint("F2")["fingerprint"] == f2["fingerprint"]


def test_render_changes_do_not_change_semantic_identity(collection):
    """what_bug_this_catches: template edits invalidate semantic evidence and approvals."""
    before = p.semantic_policy_fingerprint("F1")
    render = p.render_policy_fingerprint()
    change_component(collection, "ui")
    assert p.semantic_policy_fingerprint("F1")["fingerprint"] == before["fingerprint"]
    changed = p.render_policy_fingerprint()
    assert changed["fingerprint"] != render["fingerprint"] and changed["restart_required"]


def test_sticky_restart_and_clean_restart_same_deterministic_identity(collection, monkeypatch):
    """what_bug_this_catches: source reversion blesses a mixed process or process guards enter identity."""
    original = copy.deepcopy(collection["identity"])
    first = p.semantic_policy_fingerprint("F1")
    change_component(collection, "validate")
    assert p.semantic_policy_fingerprint("F1")["restart_required"]
    collection["identity"] = original
    reverted = p.semantic_policy_fingerprint("F1")
    assert reverted["restart_required"] and reverted["fingerprint"] == first["fingerprint"]
    assert reverted["descriptor_sha256"] != first["descriptor_sha256"]
    monkeypatch.setattr(p, "_PINS", {})  # simulate a new process, not a product reset API
    monkeypatch.setattr(p, "_RESTART", set())
    collection["started"] = 100
    restarted = p.semantic_policy_fingerprint("F1")
    assert restarted["status"] == "available" and restarted["fingerprint"] == first["fingerprint"]
    assert set(restarted["identity"]) == {"method", "parser_profile", "components", "runtime"}


@pytest.mark.parametrize("started,mtime", [(None, 1), (10, 11)])
def test_unknown_start_or_newer_source_requires_restart(collection, started, mtime):
    """what_bug_this_catches: initial disk collection is mistaken for loaded code."""
    collection["started"] = started
    collection["modified"]["parse"][0] = mtime
    assert p.semantic_policy_fingerprint("B0a")["restart_required"]


def test_missing_source_never_fabricates_hash_and_restoration_requires_restart(collection):
    """what_bug_this_catches: absent sources become current through a placeholder hash."""
    change_component(collection, "parse", missing=True)
    missing = p.semantic_policy_fingerprint("B0a")
    assert missing["fingerprint"] is None and missing["status"] == "unavailable"
    assert p.verify_fingerprint_descriptor(missing)
    change_component(collection, "parse")
    assert p.semantic_policy_fingerprint("B0a")["restart_required"]


@pytest.mark.parametrize("field,value", [("status", "restart_required"), ("fingerprint", "0" * 64),
    ("execution_verified", True), ("extra", "untrusted")])
def test_descriptor_tamper_rejected_without_collection(collection, monkeypatch, field, value):
    """what_bug_this_catches: guard-record tamper or unknown fields survive pure verification."""
    descriptor = p.semantic_policy_fingerprint("F1")
    monkeypatch.setattr(p, "_collect", lambda: pytest.fail("Verifier must remain pure"))
    assert p.verify_fingerprint_descriptor(descriptor)
    descriptor[field] = value
    assert not p.verify_fingerprint_descriptor(descriptor)


def test_disabled_factory_and_legacy_ports_never_collect(monkeypatch):
    """what_bug_this_catches: disabled setup or missing legacy pins reads disk/native metadata."""
    monkeypatch.setattr(p, "_collect", lambda: pytest.fail("No constructor/legacy collection"))
    config = ProviderConfig()
    assert create_provider(config).judge({}, {})["status"] == "unavailable"
    assert MockProvider({}).judge({}, {})["reason"] == "mock_semantic_policy_legacy"
    assert ReplayProvider({}).judge({}, {})["reason"] == "replay_semantic_policy_legacy"


def test_replay_v2_current_pin_cannot_rebind_old_receipt(collection, policy):
    """what_bug_this_catches: old source results are relabelled current after policy change."""
    config = policy()
    questions = build_questions("inventory")
    identity = replay_identity({}, questions, config=config)
    result = {"status": "ok", "origin": "mock", "model": None, "sdk_version": None,
        "answers": {key: {"type": "noul", "noul": 0.75} for key in questions},
        "usage": None, "actual_cost_usd": None}
    record = make_replay_record(identity, result, record_kind="authored_mock")
    port = FileReplayProvider([record], config=config)
    assert port.judge({}, questions)["origin"] == "replay"
    change_component(collection, "validate")
    assert port.judge({}, questions)["status"] == "unavailable"
    assert port.policy_history()[0]["allows_current_claim"] is False


def test_captured_live_requires_matching_source_result(collection, policy):
    """what_bug_this_catches: captured live provenance is rebound to a new semantic hash."""
    identity = replay_identity({}, build_questions("inventory"), config=policy())
    with pytest.raises(M3Error):
        make_replay_record(identity, {"origin": "live", "status": "ok"}, record_kind="captured_live")


def test_legacy_tape_readable_without_silent_current_rekey(collection, policy):
    """what_bug_this_catches: valid v1 history becomes a current v2 prediction."""
    questions = build_questions("inventory")
    config = policy()
    identity = replay_identity({}, questions, config=config)
    identity.pop("semantic_fingerprint")
    identity["schema_version"] = 1
    identity.pop("request_sha256")
    identity["request_sha256"] = digest("m3.portable-replay-request.v1", identity)
    result = {"status": "ok", "origin": "mock", "model": None, "sdk_version": None,
        "answers": {key: {"type": "noul", "noul": 0.75} for key in questions},
        "usage": None, "actual_cost_usd": None}
    record = make_replay_record(identity, result, record_kind="authored_mock")
    port = FileReplayProvider([record], config=config)
    assert port.policy_history()[0]["identity_schema_version"] == 1
    assert port.policy_history()[0]["semantic_fingerprint"] is None
    assert port.judge({}, questions)["reason"] == "replay_policy_legacy_or_request_not_found"


def test_parser_profile_changes_identity_and_rejects_unowned_fields(collection):
    """what_bug_this_catches: parser caps disappear from policy or arbitrary secrets enter it."""
    first = p.semantic_policy_fingerprint("B0a", {"parser": "pdfplumber", "parse_config": {"max_pages": 1}})
    second = p.semantic_policy_fingerprint("B0a", {"parser": "pdfplumber", "parse_config": {"max_pages": 2}})
    assert first["fingerprint"] != second["fingerprint"]
    with pytest.raises(M3Error):
        p.semantic_policy_fingerprint("B0a", {"token": "not-a-permitted-field"})


@pytest.mark.parametrize("supplied", ["missing", "tampered", "missing_sdk"])
def test_future_live_factory_source_gate_is_pure_and_fail_closed(collection, policy, monkeypatch, supplied):
    """what_bug_this_catches: valid config alone bypasses provenance/installed-SDK admission."""
    import rfp_matrix.providers.envelope as envelope
    import rfp_matrix.providers.live as live
    descriptor = p.semantic_policy_fingerprint("F1")
    config = policy(semantic_fingerprint=descriptor["fingerprint"])
    # Isolate this additional gate; this stub does not represent live approval.
    monkeypatch.setattr(envelope, "validate_future_live_envelope", lambda *args: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Pure factory must not collect/read keys/metadata or dispatch")
    monkeypatch.setattr(p, "_collect", forbidden)
    monkeypatch.setattr(live.importlib.metadata, "version", forbidden)
    monkeypatch.setattr(live.os.environ, "get", forbidden)
    if supplied == "missing":
        descriptor = None
    elif supplied == "tampered":
        descriptor["fingerprint"] = "0" * 64
    result = create_provider(config, ledger=object(), semantic_provenance=descriptor).judge({}, {})
    expected = "sdk_metadata_not_admitted" if supplied == "missing_sdk" else "semantic_source_provenance_required"
    assert result["reason"] == expected
