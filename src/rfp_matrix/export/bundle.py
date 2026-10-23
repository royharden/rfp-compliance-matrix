"""Snapshot -> stage -> hash -> atomic publication with durable idempotency.

Rendering never queries live rows. A frozen export may become historical while
rendering; the returned receipt rechecks the live generation. Every format warns
that currentness must be verified against the application, not a static label.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
from typing import Any, Iterable
import uuid

from rfp_matrix.review.store import ReviewError, ReviewStore, canonical, digest, safe_id, utc_now


_FORMATS = {"html": "matrix.html", "csv": "matrix.csv", "md": "matrix.md"}
_WARNING = "Frozen historical snapshot. Verify currentness against the application; later edits or corpus changes invalidate current approval."
_COLUMNS = ("row_id", "requirement", "mandatory", "conditional", "administrative", "applicability",
            "source", "evidence_status", "support", "counterevidence", "answer", "positive_commitment",
            "gap_acknowledgement", "approval", "approval_event_id", "previous_approval_event_id", "actor", "decision_time",
            "assessment_generation", "row_revision", "uncertainty", "invalidation_reason", "retired", "revision_ancestry", "unvalidated_note")


def _raw_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_output(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ReviewError("unsafe_path", "Export output cannot be a symlink.")
    resolved = candidate.resolve()
    # Generated descendants are checked separately; never follow an existing link.
    for ancestor in (candidate, *candidate.parents):
        if ancestor.is_symlink():
            raise ReviewError("unsafe_path", "Export output ancestors cannot be symlinks.")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _pointer(pointer: dict, evidence: bool = False) -> str:
    if evidence:
        where = f"{pointer.get('record_id', '?')}:{pointer.get('start')}..{pointer.get('end')}"
    else:
        where = f"{pointer.get('source_id', '?')} page {int(pointer.get('page_index', 0)) + 1}:{pointer.get('start')}..{pointer.get('end')}"
    return where + " — " + str(pointer.get("quote", ""))


def _view_rows(snapshot: dict) -> list[dict]:
    events = {event["event_id"]: event for event in snapshot["decision_events"]}
    result = []
    for row in sorted(snapshot["rows"], key=lambda item: item["row_id"]):
        req, assessment = row["requirement"], row["assessment"]
        event = events.get(row.get("decision_event_id"), {})
        approved = row["approval"] in {"approved_positive", "approved_gap"}
        result.append({
            "row_id": row["row_id"], "requirement": req.get("literal_clause", ""),
            "mandatory": req.get("mandatory"), "conditional": req.get("conditional"),
            "administrative": req.get("administrative"), "applicability": req.get("applicability", "unknown"),
            "source": "\n".join(_pointer(pointer) for pointer in req.get("anchors", [])),
            "evidence_status": assessment.get("status", "needs_review"),
            "support": "\n".join(_pointer(pointer, True) for pointer in assessment.get("support", [])),
            "counterevidence": "\n".join(_pointer(pointer, True) for pointer in assessment.get("counterevidence", [])),
            "answer": row.get("answer_text", ""),
            "positive_commitment": row.get("answer_text", "") if row["approval"] == "approved_positive" else "",
            "gap_acknowledgement": row.get("answer_text", "") if row["approval"] == "approved_gap" else "",
            "approval": row["approval"] if approved else row["approval"].upper(),
            "approval_event_id": event.get("event_id", ""), "actor": event.get("actor", ""),
            "previous_approval_event_id": row.get("previous_decision_event_id", ""),
            "decision_time": event.get("created_at", ""), "assessment_generation": row["assessment_generation"],
            "row_revision": row["row_revision"], "uncertainty": "; ".join(req.get("uncertainty", [])),
            "invalidation_reason": row.get("invalidation_reason", ""), "unvalidated_note": row.get("unvalidated_note", ""),
            "retired": bool(row.get("retired", False)), "revision_ancestry": "; ".join(row.get("revision_ancestry", [])),
        })
    return result


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    # Leading whitespace/control characters cannot bypass spreadsheet mitigation.
    probe = text.lstrip(" \t\r\n\v\f\ufeff")
    if probe.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _markdown_safe(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _render(snapshot: dict, formats: Iterable[str]) -> dict[str, bytes]:
    rows = _view_rows(snapshot)
    files: dict[str, bytes] = {}
    title = "RFP review matrix — fictional vendor evidence"
    if "csv" in formats:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: _csv_safe(row[key]) for key in _COLUMNS} for row in rows)
        files["matrix.csv"] = output.getvalue().encode("utf-8")
    if "md" in formats:
        lines = ["# " + title, "", _WARNING, "", "Export: " + snapshot["export_id"],
                 "Generation: " + snapshot["matrix_generation"], "", "| " + " | ".join(_COLUMNS) + " |",
                 "| " + " | ".join("---" for _ in _COLUMNS) + " |"]
        lines.extend("| " + " | ".join(_markdown_safe(row[key]) for key in _COLUMNS) + " |" for row in rows)
        files["matrix.md"] = ("\n".join(lines) + "\n").encode("utf-8")
    if "html" in formats:
        escaped = lambda value: html.escape("" if value is None else str(value), quote=True)
        head = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>RFP review matrix</title><style>
body{font:15px system-ui,sans-serif;margin:2rem;color:#172433;background:#f6f8fb}h1{margin-bottom:.4rem}
.warning{padding:1rem;border:1px solid #a86400;background:#fff3d4}.table-wrap{overflow:auto}
table{border-collapse:collapse;background:white}th,td{padding:.6rem;border:1px solid #ccd5df;vertical-align:top;white-space:pre-wrap;min-width:130px}
th{background:#18334e;color:white;position:sticky;top:0}.unresolved{background:#fff3d4}.approved{background:#e9f5ed}
code{overflow-wrap:anywhere}caption{text-align:left;font-weight:bold;padding:1rem 0}
</style></head><body>"""
        content = [head, "<h1>" + escaped(title) + "</h1>", "<p>Evidence review assistance; no legal determination or real vendor commitment.</p>",
                   '<p class="warning">' + escaped(_WARNING) + "</p>",
                   "<p>Export <code>" + escaped(snapshot["export_id"]) + "</code><br>Generation <code>" + escaped(snapshot["matrix_generation"]) + "</code><br>Captured " + escaped(snapshot["export_created_at"]) + "</p>",
                   '<div class="table-wrap"><table><caption>All ' + str(len(rows)) + " rows, including gaps and unresolved obligations</caption><thead><tr>",
                   "".join("<th scope=\"col\">" + escaped(key.replace("_", " ")) + "</th>" for key in _COLUMNS), "</tr></thead><tbody>"]
        for row in rows:
            css = "approved" if row["approval"] in {"approved_positive", "approved_gap"} else "unresolved"
            content.append('<tr class="' + css + '">' + "".join("<td>" + escaped(row[key]) + "</td>" for key in _COLUMNS) + "</tr>")
        content.append("</tbody></table></div></body></html>\n")
        files["matrix.html"] = "".join(content).encode("utf-8")
    return files


def _validate_snapshot(store: ReviewStore, snapshot: dict) -> None:
    events = {event["event_id"]: event for event in snapshot["decision_events"]}
    for row in snapshot["rows"]:
        if row["approval"] not in {"approved_positive", "approved_gap"}:
            continue
        event = events.get(row.get("decision_event_id"))
        if (not event or event["row_revision"] != row["row_revision"]
                or event["assessment_generation"] != snapshot["assessment_generation"]
                or event["approval"] != row["approval"]):
            raise ReviewError("approval_lineage_invalid", "Export cannot reuse approval for a different content generation.")
        store.validate_row(snapshot, row)
        status = row["assessment"]["status"]
        if row["approval"] == "approved_positive" and status != "supported":
            raise ReviewError("approval_lineage_invalid", "A non-supported row cannot populate positive commitment.")
        if row["approval"] == "approved_gap" and status not in {"cannot_answer_absent", "cannot_answer_conflicting"}:
            raise ReviewError("approval_lineage_invalid", "A gap acknowledgement cannot masquerade as a positive answer.")


def _write_file(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ReviewError("unsafe_path", "Export artifact cannot be a symlink.")
    if path.exists():
        if path.read_bytes() != data:
            raise ReviewError("artifact_conflict", "Existing export bytes differ from the committed intent.", filename=path.name)
        return
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def export_matrix(store: ReviewStore, run_id: str, output_dir: str | Path, *, request_id: str,
                  expected_matrix_generation: str, formats: Iterable[str] = ("html", "csv", "md")) -> dict:
    safe_id(request_id)
    selected = tuple(sorted(set(formats)))
    if not selected or not set(selected).issubset(_FORMATS):
        raise ReviewError("invalid_format", "Choose html, csv and/or md.")
    output = _safe_output(output_dir)
    payload_hash = digest("m3.export-request.v1", {"run_id": run_id, "expected_matrix_generation": expected_matrix_generation,
                                                "formats": selected, "output_dir": str(output), "policy": "m3.complete-inventory.v1"})
    with store.transaction(run_id) as connection:
        prior = connection.execute("SELECT * FROM export_intents WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise ReviewError("idempotency_conflict", "Export request ID has different arguments.")
            export_id = prior["export_id"]
            snapshot = json.loads(prior["snapshot"])
        else:
            snapshot = store._load(connection, run_id)
            if snapshot["matrix_generation"] != expected_matrix_generation:
                raise ReviewError("stale_generation", "Export request used an outdated matrix generation.", matrix_generation=snapshot["matrix_generation"])
            snapshot["decision_events"] = [json.loads(row[0]) for row in connection.execute("SELECT event FROM decisions WHERE run_id=? ORDER BY seq", (run_id,))]
            _validate_snapshot(store, snapshot)
            export_id = "X-" + digest("m3.export-id.v1", [store.store_id, run_id, request_id, payload_hash])[:32]
            snapshot.update(export_id=export_id, export_created_at=utc_now())
            connection.execute("INSERT INTO export_intents VALUES(?,?,?,?,?,?,?,NULL)",
                               (export_id, run_id, request_id, payload_hash, canonical(snapshot), snapshot["export_created_at"], "captured"))
    files = _render(snapshot, selected)
    artifacts = {name: {"sha256": _raw_sha(data), "size_bytes": len(data)} for name, data in sorted(files.items())}
    approvals = {row["row_id"]: row.get("decision_event_id") for row in snapshot["rows"] if row["approval"] in {"approved_positive", "approved_gap"}}
    manifest = {"schema_version": 1, "export_id": export_id, "run_id": run_id, "request_id": request_id,
                "payload_hash": payload_hash, "matrix_generation": snapshot["matrix_generation"],
                "assessment_generation": snapshot["assessment_generation"], "created_at": snapshot["export_created_at"],
                "historical_snapshot": True, "currentness_warning": _WARNING, "fictional_vendor": True,
                "row_count": len(snapshot["rows"]), "approval_event_ids": approvals,
                "unresolved_inventory": [{"row_id": row["row_id"], "approval": row["approval"],
                                          "mandatory": row["requirement"]["mandatory"]}
                                         for row in snapshot["rows"] if row["approval"] not in {"approved_positive", "approved_gap"}],
                "artifacts": artifacts, "snapshot": snapshot}
    files["manifest.json"] = (canonical(manifest) + "\n").encode("utf-8")
    all_hashes = {name: {"sha256": _raw_sha(data), "size_bytes": len(data)} for name, data in sorted(files.items())}
    with store.transaction(run_id) as connection:
        row = connection.execute("SELECT artifacts FROM export_intents WHERE export_id=?", (export_id,)).fetchone()
        if row[0] and json.loads(row[0]) != all_hashes:
            raise ReviewError("artifact_conflict", "Rendering differs from the persisted artifact hashes.")
        connection.execute("UPDATE export_intents SET artifacts=?,state='rendered' WHERE export_id=?", (canonical(all_hashes), export_id))
    stage = output / (".stage-" + export_id)
    target = output / export_id
    for path in (stage, target):
        if path.is_symlink():
            raise ReviewError("unsafe_path", "Export stage/publication cannot be a symlink.")
    if target.exists():
        if not target.is_dir():
            raise ReviewError("artifact_conflict", "Export target exists and is not a directory.")
        _verify_bytes(target, files)
    else:
        if stage.exists():
            if not stage.is_dir():
                raise ReviewError("artifact_conflict", "Stage exists and is not a directory.")
            damaged = False
            for path in stage.iterdir():
                if path.is_symlink() or path.name not in files or not path.is_file():
                    damaged = True
                    break
                if path.read_bytes() != files[path.name]:
                    damaged = True
                    break
            if damaged:
                # Never adopt mismatching bytes. Preserve interrupted staging and
                # reconstruct from the immutable SQLite snapshot into a new stage.
                quarantine = output / (".quarantine-" + export_id + "-" + uuid.uuid4().hex)
                os.rename(stage, quarantine)
        stage.mkdir(mode=0o700, exist_ok=True)
        for name, data in files.items():
            _write_file(stage / name, data)
        _verify_bytes(stage, files)
        try:
            os.rename(stage, target)
        except FileExistsError:
            _verify_bytes(target, files)
        _verify_bytes(target, files)
    with store.transaction(run_id) as connection:
        current = store._load(connection, run_id)
        historical = current["matrix_generation"] != snapshot["matrix_generation"]
        connection.execute("UPDATE export_intents SET state='published' WHERE export_id=?", (export_id,))
    return {"schema_version": 1, "export_id": export_id, "run_id": run_id, "request_id": request_id,
            "matrix_generation": snapshot["matrix_generation"], "state": "published", "bundle_dir": str(target),
            "historical": historical, "current_at_return": not historical, "currentness_warning": _WARNING,
            "artifacts": all_hashes, "created_at": snapshot["export_created_at"]}


def _verify_bytes(directory: Path, files: dict[str, bytes]) -> None:
    actual = {path.name for path in directory.iterdir()}
    if actual != set(files):
        raise ReviewError("artifact_conflict", "Export directory contains missing or unexpected files.")
    for name, data in files.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ReviewError("artifact_corrupt", "Export does not match persisted immutable bytes.", filename=name)


def verify_export(bundle_dir: str | Path) -> dict:
    directory = Path(bundle_dir).expanduser()
    if directory.is_symlink() or not directory.is_dir():
        raise ReviewError("unsafe_path", "Expected an export bundle directory.")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 50 * 1024 * 1024:
        raise ReviewError("invalid_manifest", "Missing or oversized manifest.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != 1 or not isinstance(manifest["artifacts"], dict) or not manifest["artifacts"]:
            raise ValueError("schema")
        if (manifest["snapshot"]["matrix_generation"] != manifest["matrix_generation"]
                or manifest["snapshot"]["export_id"] != manifest["export_id"]
                or len(manifest["snapshot"]["rows"]) != manifest["row_count"]):
            raise ValueError("snapshot")
        if {path.name for path in directory.iterdir()} != set(manifest["artifacts"]) | {"manifest.json"}:
            raise ValueError("unexpected files")
        formats = [key for key, filename in _FORMATS.items() if filename in manifest["artifacts"]]
        rendered = _render(manifest["snapshot"], formats)
        if set(manifest["artifacts"]) != set(rendered):
            raise ValueError("artifact set")
        for name, expected in manifest["artifacts"].items():
            if name not in _FORMATS.values():
                raise ValueError("filename")
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("artifact")
            data = path.read_bytes()
            if _raw_sha(data) != expected["sha256"] or len(data) != expected["size_bytes"]:
                raise ValueError("hash")
            if data != rendered[name]:
                raise ValueError("snapshot rendering")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ReviewError("artifact_corrupt", "Export manifest/artifact validation failed.") from exc
    return {"status": "structurally_verified", "export_id": manifest["export_id"],
            "matrix_generation": manifest["matrix_generation"], "artifact_count": len(manifest["artifacts"]),
            "runtime_currentness": "unknown_without_store", "semantic_quality": "not_evaluated"}
