"""CLI/TUI regression SOURCE ONLY. Not executed during Dirty Mode."""
from argparse import Namespace
from types import SimpleNamespace

import pytest

from rfp_matrix import cli
from rfp_matrix.review import ReviewError
from rfp_matrix.source import effective_coverage, parse_text_pages
from rfp_matrix.ui.terminal import _text, coverage_terminal


@pytest.mark.parametrize("confirmation,expected", [("INSPECTED ORIGINAL", True), ("yes", False)])
def test_coverage_terminal_binds_displayed_page_and_resolves_no_caller_actor(tmp_path, confirmation, expected):
    """what_bug_this_catches: accepting vague confirmation or a TUI-supplied actor instead of service-owned identity."""
    artifact = parse_text_pages([""], source_id="synthetic-ui", terms="Self-authored development fixture")
    summary = effective_coverage(artifact)
    calls = []
    reads = iter(["b", confirmation, "Inspected the exact original empty page", "q"])

    class FakeStore:
        root = tmp_path

        def load_run(self, _run):
            return {"artifact": artifact, "matrix_generation": "G1"}

        def coverage_state(self, _run):
            return {"matrix_generation": "G1", "effective": summary}

        def inspect_coverage(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"event_id": "C1", "event": {"actor": "resolved-inside-service"}}

    shown = []
    coverage_terminal(FakeStore(), "R", page_number=1, read=lambda _: next(reads), write=shown.append)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("R", 0, "accept_blank")
    assert kwargs["expected_artifact_sha"] == artifact["artifact_sha"]
    assert kwargs["expected_page_hash"] == summary["pages"][0]["page_sha"]
    assert kwargs["expected_matrix_generation"] == "G1" and kwargs["expected_coverage_revision"] is None
    assert kwargs["inspected_original"] is expected and "actor" not in kwargs
    assert any("empty extraction alone never proves" in line for line in shown)


def test_checkpoint_failure_reports_durable_domain_and_inert_terminal_text(monkeypatch, tmp_path):
    """what_bug_this_catches: checkpoint failure hiding a committed decision or terminal escape sequences executing."""
    def broken_graph(*_):
        raise ReviewError("checkpoint_corrupt", "Preserve damaged checkpoint")

    monkeypatch.setattr(cli, "_graph", broken_graph)
    result = cli._pause(object(), tmp_path, "R", "row")
    assert result["status"] == "checkpoint_unavailable"
    assert result["code"] == "checkpoint_corrupt" and result["domain_decisions_preserved"] is True
    rendered = _text("source\x1b[2J\x00\x9b31m")
    assert "\x1b" not in rendered and "\x00" not in rendered and "\x9b" not in rendered
    assert "\\u001b" in rendered


def test_diagnostics_never_constructs_or_migrates_a_store(monkeypatch, tmp_path, capsys):
    """what_bug_this_catches: an apparently read-only diagnostics command creating/migrating a database."""
    from rfp_matrix.review import operations

    def forbidden_store(*_):
        raise AssertionError("Diagnostics must not open the mutating ReviewStore constructor")

    monkeypatch.setattr(cli, "_store", forbidden_store)
    monkeypatch.setattr(operations, "diagnostics", lambda _: {"ok": False, "domain": "missing"})
    assert cli.dispatch(Namespace(command="diagnostics", state_dir=str(tmp_path), run=None)) == 2
    assert '"domain": "missing"' in capsys.readouterr().out
    assert not (tmp_path / "review.sqlite3").exists()


def test_no_config_stays_disabled_and_enabled_config_needs_separate_live_gate(monkeypatch, tmp_path):
    """what_bug_this_catches: choosing an AI method or config file silently activating a real provider."""
    import rfp_matrix.providers as providers

    def forbidden_factory(*_, **__):
        raise AssertionError("Factory must not run on these rejected/default paths")

    monkeypatch.setattr(providers, "create_provider", forbidden_factory)
    default = Namespace(provider_config=None, allow_live=False)
    assert cli._provider(default, tmp_path, artifact={}, corpus={}) == (None, None)
    monkeypatch.setattr(cli, "_provider_config", lambda _: SimpleNamespace(method="F1", enabled=True))
    enabled = Namespace(provider_config="nonsecret-policy.json", allow_live=False, method="F1")
    with pytest.raises(ValueError, match="separate explicit --allow-live"):
        cli._provider(enabled, tmp_path, artifact={}, corpus={})


def test_machine_conflict_envelope_retains_current_and_expected_versions():
    """what_bug_this_catches: automation losing CAS refresh values or confusing a conflict with success."""
    args = Namespace(command="inspect-coverage", run="R", request_id="C1", matrix_generation="old", coverage_revision=None)
    failure = ReviewError("coverage_conflict", "Refresh exact page", matrix_generation="new", coverage_revision="page-v2")
    result = cli.command_response(args, error=failure)
    assert result["schema_version"] == 1 and result["status"] == "conflict"
    assert result["identifiers"]["run_id"] == "R" and result["identifiers"]["request_id"] == "C1"
    assert result["versions"]["expected"]["matrix_generation"] == "old"
    assert result["versions"]["current"]["matrix_generation"] == "new"
    assert result["versions"]["current"]["coverage_revision"] == "page-v2"
    assert result["error"]["code"] == "coverage_conflict" and result["data"] is None


def test_keyless_file_replay_uses_exact_keyword_factory_and_never_live(monkeypatch, tmp_path):
    """what_bug_this_catches: positional drift in D4's replay API, mismatched hashes, or replay enabling live fallback."""
    import rfp_matrix.providers as providers
    policy = SimpleNamespace(enabled=False, method="F1", document_id="R", source_sha256="A", corpus_sha256="C", config_sha256="policy-id")
    monkeypatch.setattr(cli, "_provider_config", lambda _: policy)
    calls = []
    port = object()

    def load(path, *, config):
        calls.append((path, config))
        return port

    monkeypatch.setattr(providers, "load_replay_tape", load)
    args = Namespace(replay_tape="sample.m3-replay.json", provider_config="disabled-policy.json", allow_live=False, method="F1", run="R")
    result, identity = cli._provider(args, tmp_path, artifact={"artifact_sha": "A"}, corpus={"generation": "C"})
    assert result is port and identity and len(calls) == 1 and calls[0][1] is policy
    args.allow_live = True
    with pytest.raises(ValueError, match="forbids --allow-live"):
        cli._provider(args, tmp_path, artifact={"artifact_sha": "A"}, corpus={"generation": "C"})
    assert len(calls) == 1
