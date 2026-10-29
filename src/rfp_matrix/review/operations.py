"""Opt-in maintenance source. Importing this module performs no runtime I/O.

Domain SQLite is authoritative. Checkpoint snapshots are preserved as opaque
recovery material; this release never adopts an unpinned checkpoint schema.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .store import ReviewStore


MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 4096
_SOURCE_NAME = re.compile(r"[0-9a-f]{64}\.(json|pdf)")


def _error(code: str, message: str) -> Exception:
    from .store import ReviewError
    return ReviewError(code, message)


def _path(value: str | Path) -> Path:
    supplied = Path(value).expanduser().absolute()
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
           for p in (supplied, *supplied.parents)):
        raise _error("unsafe_path", "Maintenance paths cannot traverse links or junctions.")
    return supplied.resolve()


@contextmanager
def _readonly(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def _file_hash(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise _error("invalid_backup_file", "Expected a bounded regular backup file.")
    sha = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise _error("backup_limit", "Backup file exceeds size bound.")
            sha.update(chunk)
    return {"sha256": sha.hexdigest(), "size_bytes": count}


def _write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy(source: Path, target: Path) -> None:
    _file_hash(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        count = 0
        while chunk := reader.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise _error("backup_limit", "Copy exceeds file limit.")
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if _file_hash(source) != _file_hash(target):
        raise _error("source_changed", "File changed while preserving backup bytes.")


def _checkpoint_status(root: Path) -> dict:
    path = root / "checkpoints.sqlite3"
    if not path.exists():
        return {"state": "missing", "recovery": "rebuild_projection_from_committed_domain_events"}
    try:
        _file_hash(path)
        with _readonly(path) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return {"state": "corrupt", "recovery": "quarantine_and_rebuild_projection"}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"checkpoints", "writes"}.issubset(tables):
                return {"state": "partial_or_unsupported", "recovery": "quarantine_and_rebuild_projection"}
            # A SQLite integrity pass does not establish SDK/checkpointer compatibility.
            return {"state": "unverified_schema", "recovery": "preserve_opaque_snapshot_do_not_adopt",
                    "compatibility": "exact_dependency_pin_and_runtime_validation_required"}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"state": "corrupt_or_unreadable", "reason": type(exc).__name__,
                "recovery": "quarantine_and_rebuild_projection"}


def diagnostics(state_dir: str | Path) -> dict:
    """Read-only domain/registry audit; never creates or repairs a store."""
    from .store import matrix_generation, row_revision, decision_revision, canonical
    from rfp_matrix.source import SourceRegistry, validate_artifact
    root = _path(state_dir)
    report: dict[str, Any] = {"state_dir": str(root), "domain": "missing", "issues": [],
                              "runtime_verified": False, "checkpoint": _checkpoint_status(root)}
    ledger = root / "provider-ledger.sqlite3"
    report["provider_ledger"] = "absent_no_accounting_reconstructed"
    if ledger.exists():
        try:
            _file_hash(ledger)
            with _readonly(ledger) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise _error("provider_ledger_corrupt", "Provider accounting database integrity failed.")
            report["provider_ledger"] = "integrity_only_schema_unverified"
        except (OSError, ValueError, sqlite3.Error):
            report["provider_ledger"] = "corrupt_or_unreadable"
            report["issues"].append("provider_accounting_must_not_be_discarded_or_reset")
    db = root / "review.sqlite3"
    if not db.exists():
        report["issues"].append("authoritative_domain_missing_no_checkpoint_reconstruction")
        report["ok"] = False
        return report
    try:
        _file_hash(db)
        with _readonly(db) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise _error("corrupt_domain", "Domain integrity check failed.")
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            if meta.get("schema_version") not in {"1", "2", "3", "4"}:
                raise _error("unsupported_schema", "Unsupported domain schema; no automatic repair.")
            marker = root / "store-id.txt"
            if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != meta.get("store_id"):
                raise _error("domain_identity_mismatch", "Identity marker does not match domain.")
            snapshots = [json.loads(row[0]) for row in connection.execute("SELECT snapshot FROM runs ORDER BY run_id")]
            history_records = connection.execute("SELECT seq,snapshot FROM history ORDER BY seq").fetchall()
            by_sequence = {row["seq"]: json.loads(row["snapshot"]) for row in history_records}
            historical = list(by_sequence.values())
            exports = [json.loads(row[0]) for row in connection.execute("SELECT snapshot FROM export_intents ORDER BY export_id")]
            if len(snapshots) + len(historical) + len(exports) > 10000:
                raise _error("backup_limit", "Maintenance snapshot count exceeds supported bound.")
            artifacts: dict[str, dict] = {}
            for snapshot in snapshots + historical + exports:
                if matrix_generation(snapshot) != snapshot["matrix_generation"]:
                    raise _error("domain_lineage_invalid", "Snapshot generation does not match content.")
                artifact = validate_artifact(snapshot["artifact"], allow_historical=True)
                artifacts[artifact["artifact_sha"]] = artifact
                if "change_report" in snapshot:
                    from .store import digest
                    report_content = {key: item for key, item in snapshot["change_report"].items() if key != "context_sha"}
                    context_sha = digest("m3.change-context.v1", report_content)
                    if (context_sha != snapshot["change_report"]["context_sha"]
                            or context_sha != snapshot["change_record"]["context_sha"]):
                        raise _error("change_lineage_invalid", "Stored source/corpus change report digest differs.")
                if "coverage_revision" in snapshot:
                    from rfp_matrix.source.coverage import effective_coverage, same_effective_coverage
                    coverage = snapshot["coverage"]
                    effective = effective_coverage(artifact, coverage["events"])
                    if (not same_effective_coverage(effective, coverage["effective"])
                            or effective["summary_revision"] != snapshot["coverage_revision"]):
                        raise _error("coverage_lineage_invalid", "Frozen coverage summary does not match its full event chain.")
                for row in snapshot["rows"]:
                    if row_revision(row) != row["row_revision"]:
                        raise _error("domain_lineage_invalid", "Row revision does not match frozen content.")
                    if "decision_revision" in row and row["decision_revision"] != decision_revision(row):
                        raise _error("domain_lineage_invalid", "Decision CAS is inconsistent with row content/approval/event identity.")
                    if row["approval"] in {"approved_positive", "approved_gap"}:
                        record = connection.execute("SELECT event FROM decisions WHERE run_id=? AND event_id=?",
                                                    (snapshot["run_id"], row.get("decision_event_id"))).fetchone()
                        event = json.loads(record[0]) if record else {}
                        if (event.get("row_revision") != row["row_revision"]
                                or event.get("assessment_generation") != snapshot["assessment_generation"]
                                or event.get("approval") != row["approval"]):
                            raise _error("approval_lineage_invalid", "Snapshot approval lacks its exact domain event.")
            if meta["schema_version"] in {"2", "3", "4"}:
                from rfp_matrix.source.coverage import effective_coverage
                coverage_groups: dict[tuple[str, str], list] = {}
                for record in connection.execute("SELECT run_id,artifact_sha,event FROM coverage_events ORDER BY seq"):
                    coverage_groups.setdefault((record["run_id"], record["artifact_sha"]), []).append(json.loads(record["event"]))
                for (_run, artifact_sha), events in coverage_groups.items():
                    if artifact_sha not in artifacts:
                        raise _error("coverage_lineage_invalid", "Coverage ledger refers to an unknown historical artifact.")
                    effective_coverage(artifacts[artifact_sha], events)
            if meta["schema_version"] in {"3", "4"}:
                from .correspondence import validate_durable_history
                report["correspondence_events"] = validate_durable_history(connection, by_sequence)
            if meta["schema_version"] == "4":
                from .inventory import validate_promotion_history
                report["inventory_promotions"] = validate_promotion_history(connection, by_sequence)
            registry = SourceRegistry(root / "sources")
            for identity, artifact in artifacts.items():
                if canonical(registry.load(identity)) != canonical(artifact):
                    raise _error("artifact_corrupt", "Registry extraction differs from authoritative history.")
                if artifact["source"]["media_type"] == "application/pdf":
                    blob = root / "sources" / (artifact["source"]["raw_sha"] + ".pdf")
                    if _file_hash(blob)["sha256"] != artifact["source"]["raw_sha"]:
                        raise _error("source_corrupt", "Original PDF blob does not match immutable source hash.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise _error("domain_lineage_invalid", "Domain foreign-key lineage is inconsistent.")
            report.update(domain="consistent", store_id=meta["store_id"], schema_version=meta["schema_version"],
                          run_ids=[s["run_id"] for s in snapshots], artifacts=sorted(artifacts),
                          run_versions={s["run_id"]: {"matrix_generation": s["matrix_generation"], "assessment_generation": s["assessment_generation"],
                                                     "coverage_revision": s.get("coverage_revision")} for s in snapshots},
                          history_snapshots=len(historical), captured_exports=len(exports))
            from .currentness import policy_review
            report["policy_currentness"] = {snapshot["run_id"]: policy_review(snapshot) for snapshot in snapshots}
            report["current_approvals_verified"] = False
            report["policy_note"] = "Domain integrity is separate from policy currentness; typed provider context absent unless explicitly injected."
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        report["domain"] = getattr(exc, "code", "corrupt_or_incomplete")
        report["issues"].append(str(exc))
    report["ok"] = report["domain"] == "consistent" and report["provider_ledger"] != "corrupt_or_unreadable"
    report["checkpoint_requires_rebuild_or_validation"] = True
    return report


def backup_store(store: ReviewStore, destination: str | Path) -> dict:
    """Create a new bundle under maintenance/run locks; publish manifest last.

    Call only while other processes using this state directory are quiescent.
    Application locks cover cooperative writers; foreign SQLite writers cannot
    be authenticated here. Backup checks all frozen source references.
    """
    from .store import canonical, utc_now
    target = _path(destination)
    if target.exists() or target == store.root or store.root in target.parents:
        raise _error("destination_exists", "Choose a new backup directory outside active state.")
    with store.run_lock("M3-store-maintenance"), ExitStack() as locks:
        with closing(store._connect()) as connection:
            run_ids = [row[0] for row in connection.execute("SELECT run_id FROM runs ORDER BY run_id")]
        for run_id in run_ids:
            locks.enter_context(store.run_lock(run_id))
        before = diagnostics(store.root)
        if not before["ok"]:
            raise _error("backup_blocked", "Domain/registry diagnostics must be consistent before backup.")
        target.mkdir(parents=True, exist_ok=False)
        with _readonly(store.db_path) as source, closing(sqlite3.connect(target / "review.sqlite3")) as copy:
            source.backup(copy)
        _copy(store.root / "store-id.txt", target / "store-id.txt")
        files = ["review.sqlite3", "store-id.txt"]
        provider_ledger = store.root / "provider-ledger.sqlite3"
        if provider_ledger.exists():
            with _readonly(provider_ledger) as source, closing(sqlite3.connect(target / "provider-ledger.sqlite3")) as copy:
                source.backup(copy)
            files.append("provider-ledger.sqlite3")
        registry = _path(store.root / "sources")
        if registry.exists():
            entries = sorted(registry.iterdir())
            if len(entries) > MAX_BUNDLE_FILES - 3:
                raise _error("backup_limit", "Registry exceeds supported backup entry bound.")
            for entry in entries:
                if not _SOURCE_NAME.fullmatch(entry.name):
                    raise _error("invalid_registry", "Unexpected registry file; preserve it separately before maintenance.")
                if entry.suffix == ".json":
                    from rfp_matrix.source import SourceRegistry
                    SourceRegistry(registry).load(entry.stem)
                elif _file_hash(entry)["sha256"] != entry.stem:
                    raise _error("source_corrupt", "Registry PDF filename differs from its content hash.")
                _copy(entry, target / "sources" / entry.name)
                files.append("sources/" + entry.name)
        checkpoint = before["checkpoint"]
        if (store.root / "checkpoints.sqlite3").exists():
            # Snapshot SQLite through its API so a WAL cannot be omitted.
            if checkpoint["state"] in {"unverified_schema", "partial_or_unsupported"}:
                with _readonly(store.root / "checkpoints.sqlite3") as source, closing(sqlite3.connect(target / "checkpoints.snapshot.sqlite3")) as copy:
                    source.backup(copy)
                files.append("checkpoints.snapshot.sqlite3")
            else:
                raise _error("checkpoint_corrupt", "Preserve damaged checkpoint files manually; this backup remains incomplete.")
        copied = diagnostics(target)
        if not copied["ok"] or copied["store_id"] != store.store_id:
            raise _error("backup_inconsistent", "Copied domain and registry did not validate; no manifest published.")
        entries = {name: _file_hash(target / name) for name in files}
        if sum(item["size_bytes"] for item in entries.values()) > MAX_BUNDLE_BYTES:
            raise _error("backup_limit", "Backup exceeds aggregate size bound.")
        manifest = {"schema_version": 1, "kind": "m3.domain-backup.v1", "store_id": store.store_id,
                    "created_at": utc_now(), "files": entries, "checkpoint": checkpoint,
                    "checkpoint_restore_policy": "quarantine_only_rebuild_from_domain", "domain": copied,
                    "operator_precondition": "all_external_state_writers_quiescent", "runtime_verified": False}
        _write(target / "backup.json", (canonical(manifest) + "\n").encode("utf-8"))
    return {"status": "backup_captured", "bundle_dir": str(target), "manifest": manifest}


def _validate_bundle(source: Path) -> dict:
    manifest_path = source / "backup.json"
    if not manifest_path.is_file() or manifest_path.is_symlink() or manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise _error("backup_incomplete", "Missing or oversized final backup manifest.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    if (manifest.get("schema_version") != 1 or manifest.get("kind") != "m3.domain-backup.v1"
            or not isinstance(files, dict) or not {"review.sqlite3", "store-id.txt"}.issubset(files)
            or len(files) > MAX_BUNDLE_FILES):
        raise _error("unsupported_backup", "Unknown backup schema or incomplete file inventory.")
    total = 0
    for name, expected in files.items():
        allowed = name in {"review.sqlite3", "store-id.txt", "checkpoints.snapshot.sqlite3", "provider-ledger.sqlite3"}
        if not allowed and not (name.startswith("sources/") and _SOURCE_NAME.fullmatch(name[8:])):
            raise _error("unsafe_path", "Backup manifest contains an unsupported relative path.")
        actual = _file_hash(_path(source / name))
        total += actual["size_bytes"]
        if actual != expected or total > MAX_BUNDLE_BYTES:
            raise _error("backup_corrupt", "Backup hash, size or aggregate bound mismatch.")
    actual_files = set()
    for child in source.iterdir():
        _path(child)
        if child.name == "sources" and child.is_dir():
            actual_files.update("sources/" + entry.name for entry in child.iterdir())
        elif child.is_file():
            actual_files.add(child.name)
        else:
            raise _error("backup_corrupt", "Backup contains an unexpected directory.")
    if actual_files != set(files) | {"backup.json"}:
        raise _error("backup_corrupt", "Backup inventory contains missing or unexpected files.")
    report = diagnostics(source)
    if not report["ok"] or report["store_id"] != manifest.get("store_id"):
        raise _error("backup_corrupt", "Backup domain/registry lineage failed validation.")
    return manifest


def restore_store(bundle: str | Path, destination: str | Path) -> dict:
    """Non-destructive restore into a new local directory, never active state."""
    from .store import ReviewStore, canonical, utc_now
    source, target = _path(bundle), _path(destination)
    if (target.exists() or source == target or source in target.parents or target in source.parents
            or any("onedrive" in p.casefold() for p in target.parts)):
        raise _error("unsafe_restore_destination", "Restore requires a new machine-local destination outside the backup.")
    manifest = _validate_bundle(source)
    target.mkdir(parents=True, exist_ok=False)
    status_path = target / "restore-status.json"
    status = {"state": "incomplete", "source_store_id": manifest["store_id"], "started_at": utc_now(),
              "checkpoint_policy": "quarantined_not_active"}
    _write(status_path, (canonical(status) + "\n").encode("utf-8"))
    for name, expected in manifest["files"].items():
        _copy(source / name, target / name)
        if _file_hash(target / name) != expected:
            raise _error("backup_changed", "Backup changed after manifest validation; incomplete destination preserved.")
    report = diagnostics(target)
    if not report["ok"] or report["store_id"] != manifest["store_id"]:
        raise _error("restore_incomplete", "Restored bytes failed domain validation; destination preserved as incomplete.")
    # Mark usable only after copy validation. Epoch mutation uses the same domain
    # transaction mechanism; no checkpoint or JSON projection restores approval.
    status["state"] = "domain_validated"
    status_path.write_text(canonical(status) + "\n", encoding="utf-8")
    store = ReviewStore(target, _restoring=True)
    for run_id in report["run_ids"]:
        store.recover_graph_epoch(run_id, "explicit_restore_checkpoint_projection_reset", reset_intents=True)
    final = diagnostics(target)
    if not final["ok"]:
        raise _error("restore_incomplete", "Restored domain failed post-recovery validation.")
    status.update(state="complete", completed_at=utc_now(), recovery="new_graph_epochs_pending_domain_intents")
    with status_path.open("w", encoding="utf-8") as stream:
        stream.write(canonical(status) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"status": "restored_domain", "state_dir": str(target), "store_id": store.store_id,
            "checkpoint": "quarantined_not_adopted", "diagnostics": final, "runtime_verified": False}
