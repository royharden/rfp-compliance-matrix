"""Unrun portable replay/diagnostic source; all fixtures are authored mocks."""
from dataclasses import replace
import copy
import json

import pytest

from rfp_matrix.contracts import M3Error, canonical_json, digest
from rfp_matrix.providers import (build_questions, replay_identity, make_replay_record,
    write_replay_tape, load_replay_tape, provider_diagnostics)


def authored(config, state, questions):
    identity = replay_identity(state, questions, config=config)
    result = {"status": "ok", "origin": "mock", "model": None, "sdk_version": None,
        "answers": {key: {"type": "noul", "noul": 0.75} for key in questions},
        "usage": None, "actual_cost_usd": None}
    return make_replay_record(identity, result, record_kind="authored_mock")


def test_keyless_roundtrip_preserves_mock_origin_unknown_cost_and_exact_identity(tmp_path, policy):
    """what_bug_this_catches: importing a mock tape becomes live evidence or grants a budget."""
    config = replace(policy(), enabled=False, budget_usd="0", admission_reference="", approved_config_sha256="")
    state, questions = {"clause": "synthetic public text"}, build_questions("inventory")
    record = authored(config, state, questions)
    path = tmp_path / "synthetic.m3-replay.json"
    write_replay_tape(path, [record])
    provider = load_replay_tape(path, config=config)
    result = provider.judge(state, questions)
    assert result["status"] == "ok" and result["origin"] == "replay"
    assert result["original_origin"] == "mock" and result["model"] is None
    assert result["authenticity"] == "not_verified" and result["integrity"] == "hash_checked"
    assert result["original_observed_cost_usd"] is None
    assert result["incremental_cost_usd"] == "0" and result["actual_cost_usd"] is None
    assert provider.judge({"clause": "changed"}, questions)["status"] == "unavailable"
    changed = replace(config, corpus_sha256="c" * 64)
    assert load_replay_tape(path, config=changed).judge(state, questions)["status"] == "unavailable"
    with pytest.raises(FileExistsError):
        write_replay_tape(path, [record])


def test_tamper_extra_keys_and_replay_relabelling_fail_closed(tmp_path, policy):
    """what_bug_this_catches: integrity changes and arbitrary fields get laundered through replay."""
    config = policy()
    state, questions = {}, build_questions("inventory")
    record = authored(config, state, questions)
    altered = copy.deepcopy(record)
    altered["result"]["answers"]["mandatory"]["noul"] = 0.1
    with pytest.raises(M3Error, match="hash|fields"):
        write_replay_tape(tmp_path / "tamper.m3-replay.json", [altered])
    unknown = copy.deepcopy(record)
    unknown["result"]["headers"] = {}
    with pytest.raises(M3Error, match="unexpected"):
        write_replay_tape(tmp_path / "extra.m3-replay.json", [unknown])
    with pytest.raises(M3Error, match="relabelled"):
        make_replay_record(record["identity"], {**record["result"], "origin": "replay"}, record_kind="captured_live")
    with pytest.raises(M3Error, match="Duplicate"):
        write_replay_tape(tmp_path / "duplicate.m3-replay.json", [record, record])


def test_paths_size_and_duplicate_json_keys_are_bounded(tmp_path, policy):
    """what_bug_this_catches: tape loading reads arbitrary credential paths or unbounded JSON."""
    config = policy()
    with pytest.raises(M3Error, match="local"):
        load_replay_tape(tmp_path / "auth.json", config=config)
    huge = tmp_path / "oversize.m3-replay.json"
    huge.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(M3Error, match="2 MiB"):
        load_replay_tape(huge, config=config)
    duplicate = tmp_path / "duplicate-key.m3-replay.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(M3Error, match="Duplicate JSON"):
        load_replay_tape(duplicate, config=config)


def test_diagnostics_do_not_read_keys_sdk_or_database(monkeypatch):
    """what_bug_this_catches: offline diagnostics trigger provider setup or expose secret presence."""
    import rfp_matrix.providers.live as live
    def forbidden(*args, **kwargs):
        raise AssertionError("No key/SDK access in diagnostics")
    with monkeypatch.context() as scoped:
        scoped.setattr(live.os.environ, "get", forbidden)
        scoped.setattr(live.importlib.metadata, "version", forbidden)
        report = provider_diagnostics()
    assert report["status"] == "disabled"
    assert not report["key_checked"] and not report["dispatch_performed"] and not report["sdk_imported"]
    assert not report["live_authorization_verified"]
