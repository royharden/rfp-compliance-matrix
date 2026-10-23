"""Local source-linked review commands; all runtime I/O begins in main()."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


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


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _store(state: Path) -> Any:
    from .review import ReviewStore
    return ReviewStore(state)


def _source_context(snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    from .source import resolve_anchor
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
    if not lines:
        lines.append("No registered source anchors; this row requires review.")
    for heading, key in (("SUPPORTING FICTIONAL EVIDENCE", "support"), ("COUNTEREVIDENCE (FICTIONAL)", "counterevidence")):
        for pointer in row["assessment"].get(key, []):
            resolved = resolve_evidence(snapshot["corpus"], pointer)
            lines.append(heading + "\n" + _text(json.dumps(resolved, ensure_ascii=False, sort_keys=True)))
    return "\n\n".join(lines)


def _graph(store: Any, state: Path) -> Any:
    from .review import LangGraphReviewDriver
    return LangGraphReviewDriver(state / "checkpoints.sqlite3")


def _reconcile(store: Any, state: Path, run_id: str) -> dict[str, Any]:
    try:
        return store.reconcile(run_id, driver=_graph(store, state))
    except Exception as exc:
        if not isinstance(exc, ImportError) and getattr(exc, "code", None) != "graph_unavailable":
            raise
        return {"status": "checkpoint_unavailable", "reason": str(exc), "domain_decisions_preserved": True, "runtime_verified": False}


def _pause(store: Any, state: Path, run_id: str, row_id: str) -> dict[str, Any]:
    try:
        return _graph(store, state).pause(store, run_id, row_id)
    except Exception as exc:
        if not isinstance(exc, ImportError) and getattr(exc, "code", None) != "graph_unavailable":
            raise
        return {"status": "checkpoint_unavailable", "reason": str(exc), "runtime_verified": False}


def _revise(store: Any, run_id: str, *, operation: str, row_ids: list[str], interpretations: list[str] | None, applicability: str | None, reason: str, method: str, expected_matrix_generation: str, request_id: str) -> dict[str, Any]:
    from .review import ReviewError
    from .evidence import revise_requirements
    intent = {"operation": operation, "row_ids": row_ids, "interpretations": interpretations, "applicability": applicability, "reason": reason, "method": method}
    prior = store.revision_request(run_id, request_id)
    if prior is not None:
        payload = prior["payload"]
        if payload.get("intent") != intent or payload.get("expected_matrix_generation") != expected_matrix_generation or payload.get("reason") != reason:
            raise ReviewError("idempotency_conflict", "Revision request ID already binds a different intent.")
        return prior["snapshot"]
    current = store.load_run(run_id)
    if current["matrix_generation"] != expected_matrix_generation:
        raise ReviewError("stale_generation", "Requirement revision needs the current displayed matrix generation.")
    replacements = revise_requirements(current["artifact"], current["corpus"], current["rows"], operation=operation, row_ids=row_ids, interpretations=interpretations, applicability=applicability, reason=reason, method=method)
    return store.revise_rows(run_id, row_ids, replacements, expected_matrix_generation=expected_matrix_generation, request_id=request_id, reason=reason, intent=intent)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rfp-matrix", description="Source-linked RFP review using fictional vendor evidence. Construction source; runtime unverified.")
    parser.add_argument("--state-dir", help="Machine-local durable data directory outside OneDrive")
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
    decision = sub.add_parser("decide", help="Record one explicit decision against exact displayed revisions")
    decision.add_argument("--run", required=True)
    decision.add_argument("--row", required=True)
    decision.add_argument("--action", required=True, choices=("approve_positive", "accept_gap", "edit", "reject", "defer", "not_applies"))
    decision.add_argument("--assessment-generation", required=True)
    decision.add_argument("--row-revision", required=True)
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
    sub.add_parser("diagnostics")
    evaluation = sub.add_parser("eval", help="Score an explicitly supplied development prediction/gold packet; no live calls")
    evaluation.add_argument("--input", required=True)
    evaluation.add_argument("--out")
    evaluation.add_argument("--split", choices=("dev",), default="dev", help="Custodian-held confirmatory scoring is a separate future boundary")
    return parser


def dispatch(args: argparse.Namespace) -> int:
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
        _print({"state_dir": str(state), "fictional_vendor_evidence": True, "live_budget": "0", "runtime_verified": False, "listeners": "none", "note": "Inspect installed dependency compatibility after Dirty Mode closes; this command does not start a service."})
        return 0
    store = _store(state)
    if args.command == "import":
        from .source import SourceRegistry, parse_pdf
        from .evidence import build_matrix, load_corpus
        artifact = parse_pdf(Path(args.pdf), terms=args.terms, source_url=args.source_url)
        SourceRegistry(state / "sources").save(artifact, raw_path=Path(args.pdf))
        corpus = load_corpus(Path(args.vendor))
        matrix = build_matrix(artifact, corpus, method=args.method)
        rows = matrix["rows"]
        snapshot = store.create_run(args.run, artifact, corpus, rows, inventory_report=matrix["inventory_report"])
        summary = {key: artifact.get(key) for key in ("source", "artifact_sha", "parser", "parser_version", "complete", "issues")}
        summary["page_count"] = len(artifact.get("pages", []))
        summary["page_coverage"] = [{key: page.get(key) for key in ("page_index", "coverage", "issues")} for page in artifact.get("pages", [])]
        _print({"run_id": args.run, "source": summary, "matrix_generation": snapshot["matrix_generation"], "row_count": len(rows), "fictional_vendor_evidence": True, "method": args.method, "graph": _reconcile(store, state, args.run)})
    elif args.command == "review":
        from .ui import format_inventory, format_row, review_terminal
        if args.interactive:
            review_terminal(store, args.run, row_id=args.row, queue=args.queue, source_context=_source_context, prepare_review=lambda row_id: _pause(store, state, args.run, row_id), revise_requirement=lambda **kwargs: _revise(store, args.run, **kwargs), after_decision=lambda: _reconcile(store, state, args.run))
        else:
            snapshot = store.load_run(args.run)
            print(format_inventory(snapshot, args.queue))
            if args.row:
                row = next((r for r in snapshot["rows"] if r["row_id"] == args.row and (args.queue == "all" or r.get("approval", "pending") == args.queue)), None)
                if row is None:
                    raise ValueError("Unknown row ID in this queue.")
                print(format_row(snapshot, row))
    elif args.command == "decide":
        event = store.decide(args.run, args.row, args.action, expected_assessment_generation=args.assessment_generation, expected_row_revision=args.row_revision, request_id=args.request_id, reason=args.reason, answer_text=args.answer_text, human_validated=args.human_validated)
        _print({"event": event, "reconcile": _reconcile(store, state, args.run)})
    elif args.command == "resume":
        _print({"reconcile": _reconcile(store, state, args.run), "snapshot": store.load_run(args.run)})
    elif args.command == "revise":
        snapshot = _revise(store, args.run, operation=args.operation, row_ids=args.row, interpretations=args.interpretation, applicability=args.applicability, reason=args.reason, method=args.method, expected_matrix_generation=args.matrix_generation, request_id=args.request_id)
        _print({"run_id": args.run, "matrix_generation": snapshot["matrix_generation"], "row_ids": [row["row_id"] for row in snapshot["rows"]], "note": "Changed requirement versions are pending; preserved historical decisions do not approve new interpretations."})
    elif args.command == "history":
        _print(store.events(args.run))
    elif args.command == "inventory":
        snapshot = store.load_run(args.run)
        _print({"run_id": args.run, "artifact_sha": snapshot["artifact"]["artifact_sha"], "inventory_report": snapshot.get("inventory_report"), "note": "Original inventory classification/discard reasoning; later human changes are recorded as revision events."})
    elif args.command == "source":
        snapshot = store.load_run(args.run)
        row = next((r for r in snapshot["rows"] if r["row_id"] == args.row), None)
        if row is None:
            raise ValueError("Unknown row ID.")
        print(_source_context(snapshot, row))
    elif args.command in ("update-evidence", "update-source"):
        from .source import SourceRegistry, parse_pdf
        from .evidence import build_matrix, load_corpus, reassess_rows
        previous = store.load_run(args.run)
        artifact = previous["artifact"]
        corpus = previous["corpus"]
        reason = "new_corpus_generation"
        if args.command == "update-evidence":
            corpus = load_corpus(Path(args.vendor))
            rows = reassess_rows(artifact, corpus, previous["rows"], method=args.method)
            inventory_report = previous.get("inventory_report")
        else:
            artifact = parse_pdf(Path(args.pdf), terms=args.terms)
            SourceRegistry(state / "sources").save(artifact, raw_path=Path(args.pdf))
            reason = "cited_source_changed"
            matrix = build_matrix(artifact, corpus, method=args.method)
            rows = matrix["rows"]
            inventory_report = matrix["inventory_report"]
        _print(store.update_run(args.run, artifact, corpus, rows, expected_matrix_generation=args.matrix_generation, reason=reason, inventory_report=inventory_report))
    elif args.command == "export":
        from .export import export_matrix
        formats = ("html", "csv", "md") if args.format == "all" else (args.format,)
        _print(export_matrix(store, args.run, Path(args.out), request_id=args.request_id, expected_matrix_generation=args.matrix_generation, formats=formats))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return dispatch(parser.parse_args(argv))
    except (KeyboardInterrupt, EOFError):
        print("Review ended; committed domain decisions remain durable.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Domain errors remain explicit, including conflicts and unavailable parsers.
        code = getattr(exc, "code", "command_failed")
        print(json.dumps({"error": str(code), "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
