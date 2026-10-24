"""Local source-linked review commands; all runtime I/O begins in main()."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


class CommandUsageError(ValueError):
    code = "invalid_arguments"


class CommandParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CommandUsageError(message)


def _default_state() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return root / "rfp-matrix"


def _state_path(value: str | None) -> Path:
    path = Path(value).expanduser() if value else _default_state()
    resolved = path.resolve()
    if any("onedrive" in part.casefold() for part in resolved.parts):
        raise ValueError("Runtime state must be outside OneDrive; choose a machine-local --state-dir.")
    return resolved


def _emit_json(value: Any, *, error: bool = False) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2), file=sys.stderr if error else sys.stdout)


def command_response(args: argparse.Namespace | None, data: Any = None, *, error: Exception | None = None) -> dict:
    """One versioned command wire; document text stays JSON data, never terminal controls."""
    args = args or argparse.Namespace()
    value = data if isinstance(data, dict) else {}
    nested = next((value[key] for key in ("snapshot", "event") if isinstance(value.get(key), dict)), value)
    effective = value.get("effective", {})
    current = {key: value.get(key, nested.get(key)) for key in ("matrix_generation", "assessment_generation", "row_revision", "decision_revision", "artifact_sha", "corpus_generation")}
    current["coverage_revision"] = effective.get("summary_revision") if isinstance(effective, dict) else None
    current["correspondence_revision"] = value.get("correspondence_revision")
    expected = {key: getattr(args, key, None) for key in ("matrix_generation", "assessment_generation", "row_revision", "coverage_revision", "correspondence_revision", "artifact_sha")}
    run_arg = getattr(args, "run", None)
    status, failure = "ok", None
    if error is not None:
        code = str(getattr(error, "code", "command_failed"))
        details = getattr(error, "details", {})
        details = details if isinstance(details, dict) else {}
        current.update({key: details[key] for key in current if key in details})
        conflict = code.startswith("stale_") or code.endswith("_conflict") or code in {"run_exists", "run_busy", "store_busy"}
        unavailable = code.startswith(("checkpoint_", "replay_")) or code in {"graph_unavailable", "provider_unavailable", "optional_web_unavailable", "optional_web_dependencies_unavailable"}
        status = "conflict" if conflict else ("unavailable" if unavailable else "error")
        failure = {"code": code, "message": str(error), "details": details}
    elif value.get("status") in {"checkpoint_unavailable", "unavailable"} or value.get("ok") is False:
        status = "unavailable"
    return {"schema_version": 1, "command": getattr(args, "command", None), "status": status,
            "identifiers": {"run_id": value.get("run_id", run_arg if isinstance(run_arg, str) else None),
                            "run_ids": run_arg if isinstance(run_arg, list) else None,
                            "request_id": value.get("request_id", getattr(args, "request_id", None)),
                            "event_id": value.get("event_id", nested.get("event_id")), "export_id": value.get("export_id")},
            "versions": {"expected": expected, "current": current}, "data": data, "error": failure}


def _store(state: Path) -> Any:
    from .review import ReviewStore
    return ReviewStore(state)


def _provider_config(value: str) -> Any:
    from .providers import ProviderConfig
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("Provider config must be a bounded regular JSON policy file; never a credential file.")
    return ProviderConfig.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def _policy_mapping(value: str) -> dict:
    """Explicit nonsecret bounded policy object; no modules/credentials inferred."""
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError("Expected a bounded regular nonsecret policy JSON file.")
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate policy JSON keys are forbidden.")
            result[key] = item
        return result
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite policy JSON is forbidden.")))
    if not isinstance(data, dict):
        raise ValueError("Policy JSON must be an object.")
    return data


def _provider(args: argparse.Namespace, state: Path, *, artifact: dict, corpus: dict) -> tuple[Any, str | None]:
    from .providers import CallLedger, create_provider
    config_path = getattr(args, "provider_config", None)
    allow_live = getattr(args, "allow_live", False)
    replay_path = getattr(args, "replay_tape", None)
    envelope_path = getattr(args, "run_envelope", None)
    approval_path = getattr(args, "run_approval", None)
    if replay_path:
        if not config_path or allow_live or envelope_path or approval_path:
            raise ValueError("Replay requires an exact nonsecret --provider-config and forbids --allow-live.")
        from .providers import load_replay_tape
        config = _provider_config(config_path)
        if config.enabled:
            raise ValueError("Keyless replay policy must have enabled=false; it cannot authorize live dispatch.")
        if (config.method != args.method or config.document_id != args.run
                or config.source_sha256 != artifact["artifact_sha"] or config.corpus_sha256 != corpus["generation"]):
            raise ValueError("Replay policy must bind this exact method/run/artifact/corpus.")
        from .contracts import digest
        provider = load_replay_tape(Path(replay_path), config=config)
        return provider, digest("m3.cli-replay-config.v1", {"config": config.config_sha256, "origin": "replay"})
    if not config_path:
        if allow_live or envelope_path or approval_path:
            raise ValueError("--allow-live requires an explicitly approved provider policy file.")
        return None, None
    config = _provider_config(config_path)
    if config.method != args.method or args.method == "B0a":
        raise ValueError("Provider method must exactly match --method B0b/F1/F2.")
    if config.enabled and not allow_live:
        raise ValueError("Enabled provider policy requires a separate explicit --allow-live invocation.")
    if config.enabled and (not envelope_path or not approval_path):
        raise ValueError("Future live dispatch requires exact --run-envelope and --run-approval files; bootstrap keylessly, then preview run-envelope and explicitly reassess.")
    if not config.enabled and (envelope_path or approval_path):
        raise ValueError("Live envelope/approval flags cannot activate a disabled provider policy.")
    if config.enabled and (config.document_id != args.run or config.source_sha256 != artifact["artifact_sha"]
                           or config.corpus_sha256 != corpus["generation"]):
        raise ValueError("Enabled provider policy must bind this run ID, immutable artifact SHA and exact corpus generation.")
    # Object construction is lazy; the dispatcher alone opens the accounting DB
    # and resolves credentials on an explicitly admitted future judge() call.
    ledger = CallLedger(state / "provider-ledger.sqlite3", budget_usd=config.budget_usd,
                        input_per_million_usd=config.input_per_million_usd,
                        output_per_million_usd=config.output_per_million_usd, price_policy=config.price_policy)
    if config.enabled:
        from .source import policy_context
        semantic = policy_context(config.method)
        if semantic["status"] != "current":
            raise ValueError("Current source provenance is missing/drifted; restart or repair before future live admission.")
        return create_provider(config, ledger=ledger, run_envelope=_policy_mapping(envelope_path),
                               run_approval=_policy_mapping(approval_path), semantic_provenance=semantic["descriptor"]), config.config_sha256
    return create_provider(config, ledger=ledger), config.config_sha256


def _bind_review_policy(args: argparse.Namespace, store: Any) -> None:
    """Explicit current-policy comparison input, entirely separate from dispatch."""
    paths = getattr(args, "review_policy_config", None)
    if not paths:
        return
    from .review import ReviewError
    if args.command not in {"review", "decide", "resume", "export", "policy-status", "serve"}:
        raise ReviewError("review_policy_command", "Read-only policy is available only on current-review commands.")
    if any(getattr(args, name, None) for name in ("provider_config", "allow_live", "replay_tape", "run_envelope", "run_approval")):
        raise ReviewError("review_policy_live_mixing", "Read-only review policy cannot be combined with inference, tape or live-admission inputs.")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
        raise ReviewError("review_policy_limit", "Repeat --review-policy-config only for distinct owned run/method policies, at most 32.")
    from .providers import ProviderConfig
    from .review.policy_binding import bind_review_policies
    configs = [ProviderConfig.from_mapping(_policy_mapping(path)) for path in paths]
    run_ids = args.run if args.command == "serve" else [args.run]
    bind_review_policies(store, configs, run_ids=run_ids)


def _source_context(snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    from .source import resolve_anchor, resolve_context
    from .evidence import resolve_evidence
    from .ui.terminal import _text
    artifact = snapshot["artifact"]
    pages = artifact.get("pages", [])
    anchors = row["requirement"].get("anchors", row["requirement"].get("source_anchors", []))
    lines = []
    for anchor in anchors:
        resolve_anchor(artifact, anchor)
        page_index = anchor["page_index"]
        if page_index < 0 or page_index >= len(pages):
            raise ValueError("Anchor page is outside the frozen artifact.")
        page = pages[page_index]
        text = page.get("text", page.get("original_text", ""))
        start, end = anchor["start"], anchor["end"]
        if start < 0 or end > len(text) or start >= end or text[start:end] != anchor["quote"]:
            raise ValueError("Frozen source quote does not resolve at the registered interval.")
        lines.append(f"PAGE {page_index + 1}; codepoints [{start}:{end}]; section {_text(anchor.get('section_id', 'unknown'))}\n{_text(text[max(0, start - 180):min(len(text), end + 180)])}")
    if anchors:
        context = resolve_context(artifact, anchors)
        for item in context["anchors"]:
            if item["previous_page_tail"]:
                lines.append("PREVIOUS PAGE CONTEXT ONLY (not quoted evidence):\n" + _text(item["previous_page_tail"]))
            if item["next_page_head"]:
                lines.append("NEXT PAGE CONTEXT ONLY (not quoted evidence):\n" + _text(item["next_page_head"]))
    if not lines:
        lines.append("No registered source anchors; this row requires review.")
    for heading, key in (("SUPPORTING FICTIONAL EVIDENCE", "support"), ("COUNTEREVIDENCE (FICTIONAL)", "counterevidence")):
        for pointer in row["assessment"].get(key, []):
            resolved = resolve_evidence(snapshot["corpus"], pointer, allow_historical=True)
            lines.append(heading + "\n" + _text(json.dumps(resolved, ensure_ascii=False, sort_keys=True)))
    return "\n\n".join(lines)


def _graph(store: Any, state: Path) -> Any:
    from .review import LangGraphReviewDriver
    return LangGraphReviewDriver(state / "checkpoints.sqlite3")


def _reconcile(store: Any, state: Path, run_id: str) -> dict[str, Any]:
    try:
        return store.reconcile(run_id, driver=_graph(store, state))
    except Exception as exc:
        code = getattr(exc, "code", "")
        if not isinstance(exc, ImportError) and code != "graph_unavailable" and not code.startswith("checkpoint_"):
            raise
        return {"status": "checkpoint_unavailable", "code": code or "dependency_missing", "reason": str(exc), "domain_decisions_preserved": True, "runtime_verified": False}


def _pause(store: Any, state: Path, run_id: str, row_id: str) -> dict[str, Any]:
    try:
        return _graph(store, state).pause(store, run_id, row_id)
    except Exception as exc:
        code = getattr(exc, "code", "")
        if not isinstance(exc, ImportError) and code != "graph_unavailable" and not code.startswith("checkpoint_"):
            raise
        return {"status": "checkpoint_unavailable", "code": code or "dependency_missing", "reason": str(exc), "domain_decisions_preserved": True, "runtime_verified": False}


def _revise(store: Any, run_id: str, *, operation: str, row_ids: list[str], interpretations: list[str] | None, applicability: str | None, reason: str, method: str, expected_matrix_generation: str, request_id: str, provider: Any = None, provider_identity: str | None = None) -> dict[str, Any]:
    from .review import ReviewError
    from .evidence import revise_requirements
    intent = {"operation": operation, "row_ids": row_ids, "interpretations": interpretations, "applicability": applicability, "reason": reason, "method": method}
    if provider_identity is not None:
        intent["provider_identity"] = provider_identity
    prior = store.revision_request(run_id, request_id)
    if prior is not None:
        payload = prior["payload"]
        if payload.get("intent") != intent or payload.get("expected_matrix_generation") != expected_matrix_generation or payload.get("reason") != reason:
            raise ReviewError("idempotency_conflict", "Revision request ID already binds a different intent.")
        return prior["snapshot"]
    current = store.load_run(run_id)
    if current["matrix_generation"] != expected_matrix_generation:
        raise ReviewError("stale_generation", "Requirement revision needs the current displayed matrix generation.")
    replacements = revise_requirements(current["artifact"], current["corpus"], current["rows"], operation=operation, row_ids=row_ids, interpretations=interpretations, applicability=applicability, reason=reason, method=method, provider=provider)
    return store.revise_rows(run_id, row_ids, replacements, expected_matrix_generation=expected_matrix_generation, request_id=request_id, reason=reason, intent=intent)


def build_parser() -> argparse.ArgumentParser:
    parser = CommandParser(prog="rfp-matrix", description="Source-linked RFP review using fictional vendor evidence. Construction source; runtime unverified.")
    parser.add_argument("--state-dir", help="Machine-local durable data directory outside OneDrive")
    parser.add_argument("--output", choices=("json", "text"), default="json", help="Versioned JSON command envelopes by default; text for readable noninteractive review")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("import", help="Import an in-scope native-text PDF and fictional JSONL evidence")
    ingest.add_argument("pdf")
    ingest.add_argument("--vendor", required=True)
    ingest.add_argument("--run", required=True)
    ingest.add_argument("--terms", required=True, help="Permitted-use/redistribution terms; public hosting alone is insufficient")
    ingest.add_argument("--source-url")
    ingest.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a")
    review = sub.add_parser("review", help="List all matrix columns or interactively review a row")
    review.add_argument("--run", required=True)
    review.add_argument("--row")
    review.add_argument("--queue", choices=("all", "pending", "approved_positive", "approved_gap", "rejected", "stale"), default="all")
    review.add_argument("--interactive", action="store_true")
    for command in ("resume", "history", "inventory"):
        item = sub.add_parser(command)
        item.add_argument("--run", required=True)
    inventory_audit = sub.add_parser("inventory-audit", help="Frozen machine segment dispositions plus separate human correction lineage")
    inventory_audit.add_argument("--run", required=True)
    promote = sub.add_parser("promote-inventory", help="Promote one registered omitted/context candidate to a NEW PENDING requirement; never approve")
    promote.add_argument("--run", required=True)
    promote.add_argument("--candidate-id", required=True)
    promote.add_argument("--artifact-sha", required=True)
    promote.add_argument("--assessment-generation", required=True)
    promote.add_argument("--matrix-generation", required=True)
    promote.add_argument("--request-id", required=True)
    promote.add_argument("--reason", required=True)
    promote.add_argument("--interpretation")
    for flag in ("mandatory", "conditional", "administrative"):
        promote.add_argument("--" + flag, choices=("true", "false"), help="Explicit independent override; omit to use registered source flag")
    promote.add_argument("--applicability", choices=("applies", "unknown", "not_applies"))
    preflight = sub.add_parser("run-envelope", help="Pure prospective request/token/declared-cost bounds; no keys, ledger or dispatch")
    preflight.add_argument("--run", required=True)
    preflight.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a", help="Explicit method; default is always no-provider B0a")
    preflight.add_argument("--origin", choices=("baseline", "live", "mock", "replay"))
    preflight.add_argument("--config", help="Nonsecret exact-model/source/corpus policy JSON, required for a typed method")
    preflight.add_argument("--approval", help="Prospective approval JSON for comparison only; creates no authorization")
    preflight.add_argument("--budget-context", help="Explicit declared ledger-budget snapshot JSON; absent balance stays unknown")
    policy_status = sub.add_parser("policy-status", help="Explicit current semantic provenance; never evidence execution or trusted attestation")
    policy_status.add_argument("--run", required=True)
    recompute = sub.add_parser("reassess", help="Actually recompute all active evidence, preserve human origin, invalidate approvals; source-policy mismatch needs update-source")
    recompute.add_argument("--run", required=True)
    recompute.add_argument("--matrix-generation", required=True)
    recompute.add_argument("--request-id", required=True)
    recompute.add_argument("--reason", required=True)
    recompute.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a")
    changes = sub.add_parser("changes", help="Bounded exact source/corpus deltas; never semantic-equivalence or approval-retention advice")
    changes.add_argument("--run", required=True)
    changes.add_argument("--limit", type=int, default=50, help="Recent source/corpus update records, 1..200")
    amendments = sub.add_parser("amendments", help="Compare owned historical/current generations; lexical proposals never carry approval")
    amendments.add_argument("--run", required=True)
    amendments.add_argument("--previous-matrix-generation", help="Exact owned historical generation; default preceding assessment generation")
    correspondence_history = sub.add_parser("correspondence-history", help="Append-only human lineage history; separate from row approval")
    correspondence_history.add_argument("--run", required=True)
    correspondence_history.add_argument("--limit", type=int, default=100)
    confirm = sub.add_parser("confirm-correspondence", help="Record exact-version human requirement correspondence; never reapprove")
    confirm.add_argument("--run", required=True)
    confirm.add_argument("--relation", choices=("one_to_one", "split", "merge", "added", "removed"), required=True)
    confirm.add_argument("--old-row", action="append", default=[], help="Repeat registered ROW_ID=CONTENT_ROW_REVISION selections")
    confirm.add_argument("--new-row", action="append", default=[], help="Repeat registered ROW_ID=CONTENT_ROW_REVISION selections")
    confirm.add_argument("--old-matrix-generation", required=True)
    confirm.add_argument("--matrix-generation", required=True)
    confirm.add_argument("--old-assessment-generation", required=True)
    confirm.add_argument("--new-assessment-generation", required=True)
    confirm.add_argument("--old-artifact-sha", required=True)
    confirm.add_argument("--new-artifact-sha", required=True)
    confirm.add_argument("--correspondence-revision", help="Last committed comparison-ledger revision; omit only before first event")
    confirm.add_argument("--request-id", required=True)
    confirm.add_argument("--reason", required=True)
    decision = sub.add_parser("decide", help="Record one explicit decision against exact displayed revisions")
    decision.add_argument("--run", required=True)
    decision.add_argument("--row", required=True)
    decision.add_argument("--action", required=True, choices=("approve_positive", "accept_gap", "edit", "reject", "defer", "not_applies"))
    decision.add_argument("--assessment-generation", required=True)
    decision.add_argument("--row-revision", required=True, help="Opaque decision_revision CAS from review output; row_revision remains the content binding")
    decision.add_argument("--request-id", required=True)
    decision.add_argument("--reason", default="")
    decision.add_argument("--answer-text")
    decision.add_argument("--human-validated", action="store_true", help="Explicitly attest this exact restricted answer and its evidence after human review; never validates free prose")
    revise = sub.add_parser("revise", help="Version requirement interpretation/applicability or split/merge with source lineage")
    revise.add_argument("--run", required=True)
    revise.add_argument("--operation", choices=("interpretation", "applicability", "split", "merge"), required=True)
    revise.add_argument("--row", action="append", required=True, help="Repeat for all rows in a merge")
    revise.add_argument("--interpretation", action="append", help="Repeat for each split child; one interpretation for edit/merge")
    revise.add_argument("--applicability", choices=("applies", "unknown", "not_applies"))
    revise.add_argument("--reason", required=True)
    revise.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a")
    revise.add_argument("--matrix-generation", required=True)
    revise.add_argument("--request-id", required=True)
    export = sub.add_parser("export")
    export.add_argument("--run", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--format", choices=("all", "html", "csv", "md"), default="all")
    export.add_argument("--matrix-generation", required=True)
    export.add_argument("--request-id", required=True)
    verify = sub.add_parser("verify-export")
    verify.add_argument("bundle")
    for name, path in (("update-evidence", "vendor"), ("update-source", "pdf")):
        update = sub.add_parser(name)
        update.add_argument(path)
        update.add_argument("--run", required=True)
        update.add_argument("--matrix-generation", required=True)
        update.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a")
        if name == "update-source":
            update.add_argument("--terms", required=True)
    source = sub.add_parser("source")
    source.add_argument("--run", required=True)
    source.add_argument("--row", required=True)
    diagnostics = sub.add_parser("diagnostics", help="Read-only domain/registry/checkpoint consistency inspection")
    diagnostics.add_argument("--run", help="Also report whether this run is present in the authoritative store")
    backup = sub.add_parser("backup", help="Capture domain/registry and opaque checkpoint to a new directory; quiesce other state users")
    backup.add_argument("--out", required=True)
    restore = sub.add_parser("restore", help="Validate backup and restore to a NEW machine-local state directory")
    restore.add_argument("bundle")
    restore.add_argument("--destination", required=True)
    coverage = sub.add_parser("coverage", help="Inspect original per-page coverage and full human audit")
    coverage.add_argument("--run", required=True)
    coverage.add_argument("--interactive", action="store_true")
    coverage.add_argument("--page", type=int, help="One-based page number")
    inspect = sub.add_parser("inspect-coverage", help="Append an exact page coverage decision; actor is resolved by the application")
    inspect.add_argument("--run", required=True)
    inspect.add_argument("--page", type=int, required=True, help="One-based page number")
    inspect.add_argument("--action", choices=("accept_blank", "accept_text", "flag_unresolved"), required=True)
    inspect.add_argument("--artifact-sha", required=True)
    inspect.add_argument("--page-sha", required=True)
    inspect.add_argument("--matrix-generation", required=True)
    inspect.add_argument("--coverage-revision", help="Prior per-page decision revision; omit for first decision")
    inspect.add_argument("--request-id", required=True)
    inspect.add_argument("--reason", required=True)
    inspect.add_argument("--inspected-original", action="store_true", help="Explicit human attestation of inspecting the original page; required for acceptance")
    reassess = sub.add_parser("reassess-coverage", help="Explicitly version/reassess coverage-dependent rows after all pages resolve; never approves")
    reassess.add_argument("--run", required=True)
    reassess.add_argument("--matrix-generation", required=True)
    reassess.add_argument("--request-id", required=True)
    reassess.add_argument("--reason", required=True)
    reassess.add_argument("--method", choices=("B0a", "B0b", "F1", "F2"), default="B0a")
    provider_status = sub.add_parser("provider-status", help="Validate nonsecret provider admission policy without clients, keys, calls or store creation")
    provider_status.add_argument("--config", required=True)
    serve = sub.add_parser("serve", help="Optional foreground local reviewer; later agent-ports claim/check and interactive authentication required")
    serve.add_argument("--port", required=True, type=int, help="Explicit project-claimed loopback port; no default")
    serve.add_argument("--port-claim-reference", required=True, help="Nonsecret agent-ports claim/check receipt reference; operator attestation")
    serve.add_argument("--run", action="append", required=True, help="Repeat for each existing run granted to this local reviewer")
    serve.add_argument("--private-login-delivery", action="store_true", help="Create a verified owner-only local bootstrap file; fail closed on unsupported privacy policy; no secret terminal output")
    for command_parser in (ingest, revise, reassess, recompute):
        command_parser.add_argument("--provider-config", help="Nonsecret exact-model/SDK/price/admission JSON policy; default disabled")
        command_parser.add_argument("--allow-live", action="store_true", help="Explicit future admitted dispatch; requires approved policy and nonzero budget")
        command_parser.add_argument("--replay-tape", help="Exact file replay; requires matching disabled --provider-config; never live")
        command_parser.add_argument("--run-envelope", help="Exact inner prospective envelope JSON; future enabled dispatch only")
        command_parser.add_argument("--run-approval", help="Explicit matching whole-run approval JSON; creates no authorization itself")
    # Update commands are already registered above.
    for name in ("update-evidence", "update-source"):
        sub.choices[name].add_argument("--provider-config")
        sub.choices[name].add_argument("--allow-live", action="store_true")
        sub.choices[name].add_argument("--replay-tape")
        sub.choices[name].add_argument("--run-envelope")
        sub.choices[name].add_argument("--run-approval")
    for name in ("review", "decide", "resume", "export", "policy-status", "serve"):
        sub.choices[name].add_argument("--review-policy-config", action="append", help="Explicit disabled zero-budget nonsecret current-policy identity; repeat per run/method, never dispatches")
    evaluation = sub.add_parser("eval", help="Score an explicitly supplied development prediction/gold packet; no live calls")
    evaluation.add_argument("--input", required=True)
    evaluation.add_argument("--out")
    evaluation.add_argument("--split", choices=("dev",), default="dev", help="Custodian-held confirmatory scoring is a separate future boundary")
    return parser


def dispatch(args: argparse.Namespace) -> int:
    def _print(value: Any) -> None:
        _emit_json(command_response(args, value))

    if args.command == "provider-status":
        from .providers import provider_diagnostics
        config = _provider_config(args.config)
        _print(provider_diagnostics(config))
        return 0
    if args.command == "restore":
        from .review.operations import restore_store
        _print(restore_store(Path(args.bundle), _state_path(args.destination)))
        return 0
    if args.command == "verify-export":
        from .export import verify_export
        _print(verify_export(Path(args.bundle)))
        return 0
    if args.command == "eval":
        from .eval import score_packet
        packet = json.loads(Path(args.input).read_text(encoding="utf-8"))
        report = score_packet(packet)
        if args.out:
            output = Path(args.out)
            if output.exists():
                raise ValueError("Evaluation output exists; choose a new path to preserve prior receipts.")
            output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _print(report)
        return 0
    state = _state_path(args.state_dir)
    if args.command == "diagnostics":
        from .review.operations import diagnostics
        report = diagnostics(state)
        # Do not construct ReviewStore here: opening it can migrate a legacy DB.
        if args.run:
            report["requested_run"] = args.run
            report["requested_run_present"] = args.run in report.get("run_ids", [])
            report.update(report.get("run_versions", {}).get(args.run, {}))
        _print(report)
        return 0 if report["ok"] else 2
    store = _store(state)
    _bind_review_policy(args, store)
    if args.command == "inventory-audit":
        report = store.inventory_audit(args.run)
        if getattr(args, "output", "json") == "text":
            from .ui.terminal import format_inventory_audit
            print(format_inventory_audit(report))
        else:
            _print(report)
        return 2 if report.get("status") == "unavailable" else 0
    if args.command == "promote-inventory":
        payload = {"candidate_id": args.candidate_id, "expected_artifact_sha": args.artifact_sha,
            "expected_assessment_generation": args.assessment_generation, "expected_matrix_generation": args.matrix_generation,
            "request_id": args.request_id, "reason": args.reason}
        for name in ("interpretation", "applicability"):
            if getattr(args, name) is not None:
                payload[name] = getattr(args, name)
        for name in ("mandatory", "conditional", "administrative"):
            if getattr(args, name) is not None:
                payload[name] = getattr(args, name) == "true"
        _print(store.promote_inventory_candidate(args.run, payload))
        return 0
    if args.command == "policy-status":
        _print(store.policy_status(args.run))
        return 0
    if args.command == "reassess":
        from .review.currentness import require_execution_context
        from .review import ReviewError
        snapshot = store.load_run(args.run)
        # Exact retries are resolved before current CAS/policy or any producer.
        previous = store.revision_request(args.run, args.request_id)
        if previous is None:
            if snapshot["matrix_generation"] != args.matrix_generation:
                raise ReviewError("stale_generation", "Refresh before reassessment", matrix_generation=snapshot["matrix_generation"])
            require_execution_context(snapshot, args.method)
        provider, identity = _provider(args, state, artifact=snapshot["artifact"], corpus=snapshot["corpus"])
        _print(store.reassess(args.run, expected_matrix_generation=args.matrix_generation, request_id=args.request_id,
                             reason=args.reason, method=args.method, provider=provider, provider_identity=identity))
        return 0
    if args.command == "run-envelope":
        context = {"method": args.method}
        if args.origin is not None:
            context["origin"] = args.origin
        for option, name in (("config", "config"), ("approval", "approval"), ("budget_context", "budget_context")):
            if getattr(args, option) is not None:
                context[name] = _policy_mapping(getattr(args, option))
        report = store.run_envelope(args.run, context)
        if getattr(args, "output", "json") == "text":
            from .ui.terminal import _text
            print("Prospective algebra, not measured usage/cost or live authorization.\n" + _text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)))
        else:
            _print(report)
        return 0
    if args.command == "amendments":
        report = store.amendments(args.run, previous_matrix_generation=args.previous_matrix_generation)
        if getattr(args, "output", "json") == "text":
            from .ui.terminal import format_amendments
            print(format_amendments(report))
        else:
            _print(report)
        return 2 if report.get("status") == "unavailable" else 0
    if args.command == "correspondence-history":
        history = store.correspondence_history(args.run, limit=args.limit)
        if getattr(args, "output", "json") == "text":
            from .ui.terminal import _text
            print("Human correspondence lineage only; no approval carry.\n" + _text(json.dumps(history, ensure_ascii=False, sort_keys=True, indent=2)))
        else:
            _print(history)
        return 0
    if args.command == "confirm-correspondence":
        def references(items: list[str]) -> list[dict]:
            result = []
            for item in items:
                identity, separator, revision = item.partition("=")
                if not separator or not identity or not revision:
                    raise CommandUsageError("Row references must be registered ROW_ID=CONTENT_ROW_REVISION pairs.")
                result.append({"row_id": identity, "row_revision": revision})
            return result
        _print(store.record_correspondence(args.run, {"relation": args.relation, "old_rows": references(args.old_row), "new_rows": references(args.new_row),
            "expected_old_matrix_generation": args.old_matrix_generation, "expected_matrix_generation": args.matrix_generation,
            "expected_old_assessment_generation": args.old_assessment_generation, "expected_new_assessment_generation": args.new_assessment_generation,
            "expected_old_artifact_sha": args.old_artifact_sha, "expected_new_artifact_sha": args.new_artifact_sha,
            "expected_correspondence_revision": args.correspondence_revision, "request_id": args.request_id, "reason": args.reason}))
        return 0
    if args.command == "serve":
        from .review.server import serve_local
        _print(serve_local(store, port=args.port, port_claim_reference=args.port_claim_reference,
                          run_ids=args.run, private_login_delivery=args.private_login_delivery))
        return 0
    if args.command == "backup":
        _print(store.backup(Path(args.out)))
        return 0
    if args.command == "coverage":
        if args.interactive:
            from .ui.terminal import coverage_terminal
            coverage_terminal(store, args.run, page_number=args.page)
        else:
            result = store.coverage_state(args.run)
            if args.page is not None:
                snapshot = store.load_run(args.run)
                if not 1 <= args.page <= len(snapshot["artifact"]["pages"]):
                    raise ValueError("Page number is outside the current artifact.")
                result["original_page"] = snapshot["artifact"]["pages"][args.page - 1]
                result["original_source"] = snapshot["artifact"]["source"]
            _print(result)
        return 0
    if args.command == "inspect-coverage":
        _print(store.inspect_coverage(args.run, args.page - 1, args.action, expected_artifact_sha=args.artifact_sha,
                                     expected_page_hash=args.page_sha, expected_matrix_generation=args.matrix_generation,
                                     expected_coverage_revision=args.coverage_revision, request_id=args.request_id,
                                     reason=args.reason, inspected_original=args.inspected_original))
        return 0
    if args.command == "reassess-coverage":
        current = store.load_run(args.run)
        provider, identity = _provider(args, state, artifact=current["artifact"], corpus=current["corpus"])
        _print(store.reassess_coverage(args.run, expected_matrix_generation=args.matrix_generation, request_id=args.request_id,
                                      reason=args.reason, method=args.method, provider=provider, provider_identity=identity))
        return 0
    if args.command == "import":
        from .source import SourceRegistry, parse_pdf
        from .evidence import build_matrix, load_corpus
        from .review import ReviewError
        try:
            store.load_run(args.run)
        except ReviewError as exc:
            if exc.code != "run_missing":
                raise
        else:
            raise ReviewError("run_exists", "Run already exists; choose an explicit update with the current matrix generation.")
        artifact = parse_pdf(Path(args.pdf), terms=args.terms, source_url=args.source_url)
        SourceRegistry(state / "sources").save(artifact, raw_path=Path(args.pdf))
        corpus = load_corpus(Path(args.vendor))
        provider, identity = _provider(args, state, artifact=artifact, corpus=corpus)
        matrix = build_matrix(artifact, corpus, method=args.method, provider=provider)
        rows = matrix["rows"]
        snapshot = store.create_run(args.run, artifact, corpus, rows, policy={"schema_version": 1, "provider_config_sha256": identity}, inventory_report=matrix["inventory_report"])
        summary = {key: artifact.get(key) for key in ("source", "artifact_sha", "parser", "parser_version", "complete", "issues")}
        summary["page_count"] = len(artifact.get("pages", []))
        summary["page_coverage"] = [{key: page.get(key) for key in ("page_index", "coverage", "issues")} for page in artifact.get("pages", [])]
        _print({"run_id": args.run, "source": summary, "matrix_generation": snapshot["matrix_generation"], "assessment_generation": snapshot["assessment_generation"], "row_count": len(rows), "fictional_vendor_evidence": True, "method": args.method,
                "assessment_statuses": sorted({row["assessment"]["status"] for row in rows}),
                "assessment_origins": sorted({row["assessment"]["origin"] for row in rows}),
                "provider_config_sha256": identity, "graph": _reconcile(store, state, args.run)})
    elif args.command == "review":
        from .ui import format_inventory, format_row, review_terminal
        if args.interactive:
            review_terminal(store, args.run, row_id=args.row, queue=args.queue, source_context=_source_context, prepare_review=lambda row_id: _pause(store, state, args.run, row_id), revise_requirement=lambda **kwargs: _revise(store, args.run, **kwargs), after_decision=lambda: _reconcile(store, state, args.run))
        else:
            snapshot = store.load_run(args.run)
            from .review.currentness import presented
            snapshot = presented(snapshot, provider=store.policy_provider)
            text_mode = getattr(args, "output", "json") == "text"
            if text_mode:
                print(format_inventory(snapshot, args.queue))
            from .review.currentness import in_queue
            selected = [row for row in snapshot["rows"] if in_queue(row, args.queue)]
            result = {"run_id": args.run, "matrix_generation": snapshot["matrix_generation"],
                      "assessment_generation": snapshot["assessment_generation"], "queue": args.queue,
                      "rows": selected, "total_rows": len(snapshot["rows"]), "change_record": snapshot.get("change_record"),
                      "change_report": snapshot.get("change_report"), "coverage": store.coverage_state(args.run), "policy_status": snapshot["policy_status"]}
            if args.row:
                row = next((r for r in selected if r["row_id"] == args.row), None)
                if row is None:
                    raise ValueError("Unknown row ID in this queue.")
                result["selected_row"] = row
                result["row_revision"] = row["row_revision"]
                result["decision_revision"] = row.get("decision_revision", row["row_revision"])
                if text_mode:
                    print(format_row(snapshot, row))
            if not text_mode:
                _print(result)
    elif args.command == "decide":
        event = store.decide(args.run, args.row, args.action, expected_assessment_generation=args.assessment_generation, expected_row_revision=args.row_revision, request_id=args.request_id, reason=args.reason, answer_text=args.answer_text, human_validated=args.human_validated)
        _print({"event": event, "reconcile": _reconcile(store, state, args.run)})
    elif args.command == "resume":
        _print({"reconcile": _reconcile(store, state, args.run), "snapshot": store.load_run(args.run)})
    elif args.command == "revise":
        current = store.load_run(args.run)
        provider, identity = _provider(args, state, artifact=current["artifact"], corpus=current["corpus"])
        snapshot = _revise(store, args.run, operation=args.operation, row_ids=args.row, interpretations=args.interpretation, applicability=args.applicability, reason=args.reason, method=args.method, expected_matrix_generation=args.matrix_generation, request_id=args.request_id, provider=provider, provider_identity=identity)
        _print({"run_id": args.run, "matrix_generation": snapshot["matrix_generation"], "assessment_generation": snapshot["assessment_generation"], "row_revisions": {row["row_id"]: row["row_revision"] for row in snapshot["rows"]}, "row_ids": [row["row_id"] for row in snapshot["rows"]], "note": "Changed requirement versions are pending; preserved historical decisions do not approve new interpretations."})
    elif args.command == "history":
        _print(store.events(args.run))
    elif args.command == "inventory":
        snapshot = store.load_run(args.run)
        _print({"run_id": args.run, "artifact_sha": snapshot["artifact"]["artifact_sha"], "inventory_report": snapshot.get("inventory_report"), "note": "Original inventory classification/discard reasoning; later human changes are recorded as revision events."})
    elif args.command == "changes":
        _print(store.change_history(args.run, limit=args.limit))
    elif args.command == "source":
        snapshot = store.load_run(args.run)
        from .review.currentness import presented
        snapshot = presented(snapshot, provider=store.policy_provider)
        row = next((r for r in snapshot["rows"] if r["row_id"] == args.row), None)
        if row is None:
            raise ValueError("Unknown row ID.")
        context = _source_context(snapshot, row)
        if getattr(args, "output", "json") == "text":
            print(context)
        else:
            _print({"run_id": args.run, "row_id": row["row_id"], "row_revision": row["row_revision"],
                    "matrix_generation": snapshot["matrix_generation"], "artifact_sha": snapshot["artifact"]["artifact_sha"], "context_text": context,
                    "policy_status": row["policy_status"], "literal_history_only": True})
    elif args.command in ("update-evidence", "update-source"):
        from .review import ReviewError
        from .source import SourceRegistry, parse_pdf
        from .evidence import build_matrix, load_corpus, reassess_rows
        previous = store.load_run(args.run)
        artifact = previous["artifact"]
        corpus = previous["corpus"]
        reason = "new_corpus_generation"
        if previous["matrix_generation"] != args.matrix_generation:
            raise ReviewError("stale_generation", "Update requires the current displayed matrix generation before assessment.", matrix_generation=previous["matrix_generation"])
        if args.command == "update-evidence":
            corpus = load_corpus(Path(args.vendor))
            provider, identity = _provider(args, state, artifact=artifact, corpus=corpus)
            rows = reassess_rows(artifact, corpus, previous["rows"], method=args.method, provider=provider)
            inventory_report = previous.get("inventory_report")
        else:
            artifact = parse_pdf(Path(args.pdf), terms=args.terms)
            SourceRegistry(state / "sources").save(artifact, raw_path=Path(args.pdf))
            reason = "cited_source_changed"
            provider, identity = _provider(args, state, artifact=artifact, corpus=corpus)
            matrix = build_matrix(artifact, corpus, method=args.method, provider=provider)
            rows = matrix["rows"]
            inventory_report = matrix["inventory_report"]
        _print(store.update_run(args.run, artifact, corpus, rows, expected_matrix_generation=args.matrix_generation, reason=reason, inventory_report=inventory_report, provider_config_sha256=identity))
    elif args.command == "export":
        from .export import export_matrix
        formats = ("html", "csv", "md") if args.format == "all" else (args.format,)
        _print(export_matrix(store, args.run, Path(args.out), request_id=args.request_id, expected_matrix_generation=args.matrix_generation, formats=formats))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = None
    try:
        args = parser.parse_args(argv)
        return dispatch(args)
    except (KeyboardInterrupt, EOFError):
        result = command_response(args, {"domain_decisions_preserved": True, "reason": "review_interrupted"})
        result["status"] = "interrupted"
        _emit_json(result, error=True)
        return 130
    except Exception as exc:
        # Domain errors remain explicit, including conflicts and unavailable parsers.
        _emit_json(command_response(args, error=exc), error=True)
        return 2
