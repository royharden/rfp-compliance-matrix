"""Durable, compare-and-swap review decisions and synchronous resume intents.

No database is opened on module import. Runtime state belongs outside the source
checkout and OneDrive. Every authoritative mutation is committed in SQLite;
checkpoints and optional JSON projections cannot create decisions.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator
import uuid

from rfp_matrix.contracts import canonical_json as canonical, digest, validate_record


class ReviewError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or value in {".", ".."}:
        raise ReviewError("invalid_id", "Use a bounded logical ID, never a path.")
    return value


def _hash_artifact(artifact: dict) -> str:
    return str(artifact.get("artifact_sha") or artifact.get("artifact_sha256") or digest("m3.artifact.v1", artifact))


def _hash_corpus(corpus: dict) -> str:
    return str(corpus.get("generation") or corpus.get("corpus_generation") or digest("m3.corpus.v1", corpus))


def assessment_generation(artifact: dict, corpus: dict, policy: dict) -> str:
    return digest("m3.assessment-generation.v1", {
        "artifact": _hash_artifact(artifact), "corpus": _hash_corpus(corpus), "policy": policy,
    })


def row_revision(row: dict) -> str:
    content = {key: row.get(key) for key in (
        "assessment_generation", "requirement", "assessment", "answer_text", "validation", "unvalidated_note",
        "dependency_revision", "retired", "revision_ancestry",
    )}
    if "human_origin" in row:
        content["human_origin"] = row["human_origin"]
    return digest("m3.row-revision.v1", content)


def decision_revision(row: dict) -> str:
    """Opaque decision CAS, separate from content/approval citation identity."""
    if row.get("decision_event_id") is None and row.get("approval", "pending") == "pending":
        return row["row_revision"]
    return digest("m3.row-decision-cas.v1", {"content_revision": row["row_revision"],
                  "approval": row["approval"], "decision_event_id": row.get("decision_event_id")})


def matrix_generation(snapshot: dict) -> str:
    content = {
        "assessment_generation": snapshot["assessment_generation"],
        "rows": [{"row_id": row["row_id"], "row_revision": row["row_revision"],
                  "approval": row["approval"], "decision_event_id": row.get("decision_event_id")}
                 for row in sorted(snapshot["rows"], key=lambda value: value["row_id"])],
    }
    # Preserve legacy generations until the first explicit coverage event.
    if "coverage_revision" in snapshot:
        content["coverage_revision"] = snapshot["coverage_revision"]
    return digest("m3.matrix-generation.v1", content)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    kind TEXT NOT NULL, created_at TEXT NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL, row_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
    event TEXT NOT NULL, UNIQUE(run_id, request_id));
CREATE TABLE IF NOT EXISTS resume_intents(event_id TEXT PRIMARY KEY REFERENCES decisions(event_id),
    run_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', detail TEXT);
CREATE TABLE IF NOT EXISTS applied_events(event_id TEXT PRIMARY KEY REFERENCES decisions(event_id),
    applied_at TEXT NOT NULL, epoch INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_epochs(run_id TEXT NOT NULL, epoch INTEGER NOT NULL, reason TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(run_id, epoch));
CREATE TABLE IF NOT EXISTS presentations(request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
    row_id TEXT NOT NULL, assessment_generation TEXT NOT NULL, row_revision TEXT NOT NULL,
    epoch INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revision_requests(run_id TEXT NOT NULL, request_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL, event TEXT NOT NULL, snapshot TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(run_id, request_id));
CREATE TABLE IF NOT EXISTS export_intents(export_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
    request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL, state TEXT NOT NULL, artifacts TEXT, UNIQUE(run_id, request_id));
CREATE TABLE IF NOT EXISTS coverage_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL, artifact_sha TEXT NOT NULL,
    page_index INTEGER NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, event TEXT NOT NULL, UNIQUE(run_id, request_id));
CREATE TABLE IF NOT EXISTS correspondence_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    comparison_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
    payload TEXT NOT NULL, old_history_seq INTEGER NOT NULL REFERENCES history(seq),
    new_history_seq INTEGER NOT NULL REFERENCES history(seq), event TEXT NOT NULL,
    UNIQUE(run_id, request_id));
CREATE TABLE IF NOT EXISTS inventory_promotions(seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL,
    candidate_id TEXT NOT NULL, artifact_sha TEXT NOT NULL, row_id TEXT NOT NULL,
    history_seq INTEGER NOT NULL REFERENCES history(seq), event TEXT NOT NULL,
    UNIQUE(run_id, request_id), UNIQUE(run_id, artifact_sha, candidate_id));
"""


class ReviewStore:
    """One local-reviewer store. Actor identity is derived here, never from a row."""

    def __init__(self, state_dir: str | Path, *, _restoring: bool = False, policy_provider: Any = None):
        # Trusted object only; never constructed, called or inferred from requests.
        self.policy_provider = policy_provider
        supplied = Path(state_dir).expanduser()
        if supplied.is_symlink():
            raise ReviewError("unsafe_path", "State directory must not be a symlink.")
        self.root = supplied.resolve()
        restore_status = self.root / "restore-status.json"
        if restore_status.exists():
            if restore_status.is_symlink():
                raise ReviewError("unsafe_path", "Restore status cannot be a symlink.")
            state = json.loads(restore_status.read_text(encoding="utf-8"))
            if state.get("state") != "complete" and not (_restoring and state.get("state") == "domain_validated"):
                raise ReviewError("restore_incomplete", "Restore is incomplete; preserve it and choose a new destination.")
        if "onedrive" in {part.lower() for part in self.root.parts}:
            raise ReviewError("unsafe_state_location", "Use machine-local state outside OneDrive.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "review.sqlite3"
        if self.db_path.is_symlink():
            raise ReviewError("unsafe_path", "Domain database must not be a symlink.")
        self._mutex = threading.RLock()
        self._lock_depth: dict[str, int] = {}
        new_database = not self.db_path.exists()
        self._allow_create = new_database
        marker = self.root / "store-id.txt"
        if marker.is_symlink():
            raise ReviewError("unsafe_path", "Domain identity marker must not be a symlink.")
        if new_database and marker.exists():
            raise ReviewError("domain_missing", "Domain database is missing; restore it rather than rebuilding from checkpoints.")
        try:
            with closing(self._connect()) as connection:
                if new_database:
                    connection.executescript(_SCHEMA)
                    connection.execute("INSERT INTO meta VALUES('schema_version', '4')")
                    connection.execute("INSERT INTO meta VALUES('store_id', ?)", (str(uuid.uuid4()),))
                else:
                    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    if not row or row[0] not in {"1", "2", "3", "4"}:
                        raise ReviewError("unsupported_schema", "Unknown domain schema; refusing automatic rebuild.")
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ReviewError("corrupt_domain", "Domain database integrity check failed.")
                    if row[0] in {"1", "2", "3"}:
                        # Additive upgrade only; no decision/history is rewritten.
                        connection.executescript("BEGIN IMMEDIATE;" + _SCHEMA +
                                                 "UPDATE meta SET value='4' WHERE key='schema_version'; COMMIT;")
                self.store_id = connection.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise ReviewError("corrupt_domain", "Cannot read authoritative domain database.") from exc
        self._allow_create = False
        self.actor = "local:" + getpass.getuser() + ":" + self.store_id
        if marker.exists() and marker.read_text(encoding="utf-8") != self.store_id:
            raise ReviewError("domain_identity_mismatch", "Database and local store identity do not match.")
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(self.store_id)
                handle.flush()
                os.fsync(handle.fileno())

    def _connect(self) -> sqlite3.Connection:
        if self.db_path.is_symlink():
            raise ReviewError("unsafe_path", "Authoritative database cannot become a symlink.")
        mode = "rwc" if getattr(self, "_allow_create", False) else "rw"
        try:
            connection = sqlite3.connect(self.db_path.as_uri() + "?mode=" + mode, uri=True, timeout=0, isolation_level=None)
        except sqlite3.OperationalError as exc:
            raise ReviewError("domain_unavailable", "Authoritative database is missing or unavailable; checkpoints cannot recreate it.") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def run_lock(self, run_id: str) -> Iterator[None]:
        """OS-held lock releases on process death; lock files are never age-deleted."""
        safe_id(run_id)
        with self._mutex:
            if self._lock_depth.get(run_id, 0):
                self._lock_depth[run_id] += 1
                try:
                    yield
                finally:
                    self._lock_depth[run_id] -= 1
                return
            path = self.root / ("run-" + digest("lock", run_id)[:32] + ".lock")
            if path.is_symlink():
                raise ReviewError("unsafe_path", "Run lock must not be a symlink.")
            handle = path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ReviewError("run_busy", "Another CLI process owns this review run.") from exc
                self._lock_depth[run_id] = 1
                try:
                    yield
                finally:
                    self._lock_depth.pop(run_id, None)
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    @contextmanager
    def transaction(self, run_id: str) -> Iterator[sqlite3.Connection]:
        with self.run_lock("M3-store-maintenance"), self.run_lock(run_id):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except sqlite3.OperationalError as exc:
                connection.rollback()
                raise ReviewError("store_busy", "The domain database is busy or unavailable.") from exc
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _load(self, connection: sqlite3.Connection, run_id: str) -> dict:
        record = connection.execute("SELECT snapshot FROM runs WHERE run_id=?", (safe_id(run_id),)).fetchone()
        if not record:
            raise ReviewError("run_missing", "No authoritative run exists.", run_id=run_id)
        return json.loads(record[0])

    def _save(self, connection: sqlite3.Connection, snapshot: dict, kind: str) -> None:
        for row in snapshot["rows"]:
            if "decision_revision" in row:
                # Do not change an unrelated legacy row's effective fallback CAS.
                row["decision_revision"] = decision_revision(row)
        snapshot["matrix_generation"] = matrix_generation(snapshot)
        encoded = canonical(snapshot)
        connection.execute("INSERT INTO runs(run_id,snapshot) VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET snapshot=excluded.snapshot",
                           (snapshot["run_id"], encoded))
        connection.execute("INSERT INTO history(run_id,kind,created_at,snapshot) VALUES(?,?,?,?)",
                           (snapshot["run_id"], kind, utc_now(), encoded))

    def _prepare(self, run_id: str, artifact: dict, corpus: dict, rows: list[dict], policy: dict,
                 inventory_report: dict | None = None) -> dict:
        artifact = validate_record("artifact", artifact)
        corpus = validate_record("corpus", corpus)
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ReviewError("row_limit", "Expected at most 2,000 rows.")
        policy = deepcopy(policy)
        policy["row_policies"] = sorted({canonical({
            "requirement": row.get("requirement", {}).get("policy_hash"),
            "assessment": row.get("assessment", {}).get("policy_hash"),
            "method": row.get("assessment", {}).get("method"),
            "model": row.get("assessment", {}).get("model"),
        }) for row in rows})
        generation = assessment_generation(artifact, corpus, policy)
        prepared = []
        seen = set()
        for original in rows:
            row = deepcopy(original)
            row["requirement"] = validate_record("requirement", row["requirement"])
            row["assessment"] = validate_record("assessment", row["assessment"])
            row_id = safe_id(row.get("row_id") or row.get("requirement", {}).get("requirement_id") or row.get("requirement", {}).get("id", ""))
            if row_id in seen:
                raise ReviewError("duplicate_row", "Row IDs must be unique.")
            seen.add(row_id)
            row.update(row_id=row_id, assessment_generation=generation, approval="pending", decision_event_id=None)
            row.setdefault("unvalidated_note", "")
            row["row_revision"] = row_revision(row)
            row["decision_revision"] = decision_revision(row)
            prepared.append(row)
        result = {"schema_version": 1, "run_id": safe_id(run_id), "artifact": deepcopy(artifact),
                  "corpus": deepcopy(corpus), "policy": deepcopy(policy), "rows": prepared,
                  "assessment_generation": generation, "created_at": utc_now(),
                  "inventory_report": deepcopy(inventory_report)}
        result["matrix_generation"] = matrix_generation(result)
        return result

    def create_run(self, run_id: str, artifact: dict, corpus: dict, rows: list[dict], *, policy: dict | None = None,
                   inventory_report: dict | None = None) -> dict:
        snapshot = self._prepare(run_id, artifact, corpus, rows, policy or {"schema_version": 1}, inventory_report)
        with self.transaction(run_id) as connection:
            existing = connection.execute("SELECT snapshot FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                raise ReviewError("run_exists", "Run already exists; use update_run with its current generation.")
            self._save(connection, snapshot, "created")
            connection.execute("INSERT INTO graph_epochs VALUES(?,0,'initial',?)", (run_id, utc_now()))
        return snapshot

    def load_run(self, run_id: str) -> dict:
        with closing(self._connect()) as connection:
            return self._load(connection, run_id)

    def _coverage(self, connection: sqlite3.Connection, snapshot: dict) -> dict:
        from rfp_matrix.source.coverage import effective_coverage
        records = connection.execute(
            "SELECT event_id,created_at,event FROM coverage_events WHERE run_id=? AND artifact_sha=? ORDER BY seq",
            (snapshot["run_id"], _hash_artifact(snapshot["artifact"])),
        ).fetchall()
        events = [json.loads(row["event"]) for row in records]
        return {"events": events, "ledger": [{"event_id": row["event_id"], "created_at": row["created_at"]} for row in records],
                "effective": effective_coverage(snapshot["artifact"], events)}

    def coverage_state(self, run_id: str) -> dict:
        """Artifact-specific full ledger and CAS revisions; no actor parameter."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            snapshot = self._load(connection, run_id)
            return {"run_id": run_id, "matrix_generation": snapshot["matrix_generation"], **self._coverage(connection, snapshot)}

    def amendments(self, run_id: str, *, previous_matrix_generation: str | None = None) -> dict:
        from .correspondence import amendments
        return amendments(self, run_id, previous_matrix_generation=previous_matrix_generation)

    def record_correspondence(self, run_id: str, payload: dict) -> dict:
        from .correspondence import record_correspondence
        return record_correspondence(self, run_id, payload)

    def correspondence_history(self, run_id: str, *, limit: int = 100) -> dict:
        from .correspondence import correspondence_history
        return correspondence_history(self, run_id, limit=limit)

    def inventory_audit(self, run_id: str) -> dict:
        from .inventory import inventory_audit_state
        return inventory_audit_state(self, run_id)

    def promote_inventory_candidate(self, run_id: str, payload: dict) -> dict:
        from .inventory import promote_inventory_candidate
        return promote_inventory_candidate(self, run_id, payload)

    def run_envelope(self, run_id: str, policy_context: dict) -> dict:
        from .preflight import run_envelope
        return run_envelope(self, run_id, policy_context)

    def policy_status(self, run_id: str) -> dict:
        from .currentness import policy_review
        snapshot = self.load_run(run_id)
        return {"run_id": run_id, "matrix_generation": snapshot["matrix_generation"],
                "assessment_generation": snapshot["assessment_generation"],
                "policy_status": policy_review(snapshot, provider=self.policy_provider), "runtime_verified": False}

    def reassess(self, run_id: str, **kwargs) -> dict:
        from .reassessment import reassess
        return reassess(self, run_id, **kwargs)

    def inspect_coverage(self, run_id: str, page_index: int, action: str, *, expected_artifact_sha: str,
                         expected_page_hash: str, expected_matrix_generation: str,
                         expected_coverage_revision: str | None, request_id: str, reason: str,
                         inspected_original: bool = False) -> dict:
        """Append one source-owned decision with application actor, CAS and retry identity."""
        from rfp_matrix.source.coverage import coverage_page_sha, make_coverage_decision
        safe_id(request_id)
        payload = {"run_id": run_id, "page_index": page_index, "action": action, "actor": self.actor,
                   "artifact_sha": expected_artifact_sha, "page_sha": expected_page_hash,
                   "expected_matrix_generation": expected_matrix_generation,
                   "expected_revision": expected_coverage_revision, "reason": reason,
                   "inspected_original": inspected_original}
        payload_hash = digest("m3.coverage-request.v1", payload)
        with self.transaction(run_id) as connection:
            prior = connection.execute("SELECT * FROM coverage_events WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise ReviewError("idempotency_conflict", "Coverage request ID already binds another intent.")
                current = self._load(connection, run_id)
                return {"event": json.loads(prior["event"]), "event_id": prior["event_id"], "created_at": prior["created_at"], "replayed": True,
                        "matrix_generation": current["matrix_generation"], "historical_event": prior["artifact_sha"] != _hash_artifact(current["artifact"]),
                        **self._coverage(connection, current)}
            for table in ("decisions", "revision_requests", "export_intents", "correspondence_events", "inventory_promotions"):
                if connection.execute(f"SELECT 1 FROM {table} WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone():
                    raise ReviewError("idempotency_conflict", "Request ID already belongs to another operation.")
            snapshot = self._load(connection, run_id)
            if snapshot["matrix_generation"] != expected_matrix_generation or _hash_artifact(snapshot["artifact"]) != expected_artifact_sha:
                raise ReviewError("stale_generation", "Inspect the current artifact and matrix before recording coverage.", matrix_generation=snapshot["matrix_generation"])
            if coverage_page_sha(snapshot["artifact"], page_index) != expected_page_hash:
                raise ReviewError("stale_coverage", "Page content/structure hash changed.")
            state = self._coverage(connection, snapshot)
            previous = next((event for event in reversed(state["events"]) if event["page_index"] == page_index), None)
            if expected_coverage_revision != (previous["revision"] if previous else None):
                raise ReviewError("coverage_conflict", "Inspect the current page decision before replacing it.",
                                  coverage_revision=previous["revision"] if previous else None,
                                  matrix_generation=snapshot["matrix_generation"])
            event = make_coverage_decision(snapshot["artifact"], page_index=page_index, decision=action, reason=reason,
                                           actor=self.actor, request_id=request_id, expected_revision=expected_coverage_revision,
                                           previous=previous, inspected_original=inspected_original)
            event_id = "C-" + digest("m3.coverage-event.v1", [self.store_id, run_id, request_id, payload_hash])[:32]
            created_at = utc_now()
            connection.execute("INSERT INTO coverage_events(event_id,run_id,artifact_sha,page_index,request_id,payload_hash,created_at,event) VALUES(?,?,?,?,?,?,?,?)",
                               (event_id, run_id, expected_artifact_sha, page_index, request_id, payload_hash, created_at, canonical(event)))
            coverage = self._coverage(connection, snapshot)
            snapshot["coverage"] = coverage
            snapshot["coverage_revision"] = coverage["effective"]["summary_revision"]
            for row in snapshot["rows"]:
                if row.get("retired"):
                    continue
                if row.get("decision_event_id"):
                    row["previous_decision_event_id"] = row["decision_event_id"]
                row.update(approval="stale", decision_event_id=None, invalidation_reason="coverage_inspection_changed",
                           dependency_revision=snapshot["coverage_revision"])
                row["row_revision"] = row_revision(row)
            self._save(connection, snapshot, "coverage_inspection")
            return {"event": event, "event_id": event_id, "created_at": created_at, "replayed": False,
                    "matrix_generation": snapshot["matrix_generation"], **coverage}

    def backup(self, destination: str | Path) -> dict:
        from .operations import backup_store
        return backup_store(self, destination)

    def diagnostics(self) -> dict:
        from .operations import diagnostics
        return diagnostics(self.root)

    def reassess_coverage(self, run_id: str, *, expected_matrix_generation: str, request_id: str,
                          reason: str, method: str = "B0a", provider: Any = None,
                          provider_identity: str | None = None) -> dict:
        """Explicitly version coverage-dependent requirements; never auto-approve."""
        from rfp_matrix.evidence.engine import assess_requirement, _answer
        intent = {"operation": "coverage_reassessment", "method": method, "provider_identity": provider_identity,
                  "reason": reason, "expected_matrix_generation": expected_matrix_generation}
        prior = self.revision_request(run_id, request_id)
        if prior:
            if prior["payload"].get("intent") != intent:
                raise ReviewError("idempotency_conflict", "Reassessment ID belongs to a different intent.")
            return prior["snapshot"]
        snapshot = self.load_run(run_id)
        if snapshot["matrix_generation"] != expected_matrix_generation:
            raise ReviewError("stale_generation", "Coverage reassessment needs the current matrix.")
        state = self.coverage_state(run_id)
        summary = state["effective"]
        if not summary["complete"] or summary.get("artifact_issues"):
            raise ReviewError("coverage_incomplete", "All pages and document-level issues must be resolved before reassessment.")
        replacements = []
        for original in snapshot["rows"]:
            if original.get("retired"):
                continue
            row = deepcopy(original)
            requirement = row["requirement"]
            requirement.pop("revision", None)
            # Only source inspection flags can be discharged here. Split/classification
            # uncertainty and absent/conflicting evidence keep their original semantics.
            requirement["uncertainty"] = [flag for flag in requirement["uncertainty"]
                                           if flag not in {"source_coverage_incomplete", "page_requires_inspection"}]
            requirement["policy_hash"] = digest("m3.coverage-reassessment-policy.v1", {
                "prior_policy": requirement["policy_hash"], "coverage_revision": summary["summary_revision"]})
            requirement["revision"] = digest("m3.requirement-revision.v1", requirement)
            requirement = validate_record("requirement", requirement)
            assessment = assess_requirement(requirement, snapshot["corpus"], method=method, provider=provider)
            row.update(requirement=requirement, assessment=assessment, answer_text=_answer(assessment),
                       validation=assessment["validation"], unvalidated_note="")
            # Preserve reviewer prose as historical snapshot only; regenerated answers
            # are explicitly new restricted templates, never an attestation of edits.
            replacements.append(row)
        return self.revise_rows(run_id, [row["row_id"] for row in replacements], replacements,
                                expected_matrix_generation=expected_matrix_generation, request_id=request_id,
                                reason=reason, intent=intent)

    def events(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return [json.loads(row[0]) for row in connection.execute("SELECT event FROM decisions WHERE run_id=? ORDER BY seq", (run_id,))]

    def change_history(self, run_id: str, *, limit: int = 50) -> dict:
        """Bounded source/corpus update records, never approval-retention advice."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ReviewError("input_limit", "Change-history limit must be 1..200.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            snapshot = self._load(connection, run_id)
            total = connection.execute("SELECT COUNT(*) FROM history WHERE run_id=? AND kind LIKE 'source_update:%'", (run_id,)).fetchone()[0]
            records = connection.execute("SELECT seq,kind,created_at,snapshot FROM history WHERE run_id=? AND kind LIKE 'source_update:%' ORDER BY seq DESC LIMIT ?", (run_id, limit)).fetchall()
            entries = []
            for record in reversed(records):
                frozen = json.loads(record["snapshot"])
                entries.append({"history_seq": record["seq"], "kind": record["kind"], "created_at": record["created_at"],
                                "matrix_generation": frozen["matrix_generation"], "record": frozen.get("change_record"),
                                "changes": frozen.get("change_report"), "legacy_context_unavailable": "change_report" not in frozen})
            return {"run_id": run_id, "matrix_generation": snapshot["matrix_generation"], "updates": entries,
                    "total_updates": total, "truncated": total > len(entries), "limit": limit}

    def _row(self, snapshot: dict, row_id: str) -> dict:
        for row in snapshot["rows"]:
            if row["row_id"] == row_id:
                return row
        raise ReviewError("row_missing", "Unknown row.", row_id=row_id)

    def validate_row(self, snapshot: dict, row: dict) -> None:
        """Resolve every literal source/evidence anchor through producer-owned code."""
        from rfp_matrix.source import resolve_anchor
        from rfp_matrix.evidence import resolve_evidence
        requirement = row["requirement"]
        anchors = requirement.get("anchors", requirement.get("source_anchors", []))
        if not anchors:
            raise ReviewError("invalid_citation", "Requirement has no source anchor.")
        for anchor in anchors:
            resolve_anchor(snapshot["artifact"], anchor)
        assessment = row.get("assessment", {})
        for key in ("support", "counterevidence"):
            for item in assessment.get(key, []):
                resolve_evidence(snapshot["corpus"], item.get("anchor", item))

    def decide(self, run_id: str, row_id: str, action: str, *, expected_assessment_generation: str,
               expected_row_revision: str, request_id: str, reason: str = "", answer_text: str | None = None,
               human_validated: bool = False) -> dict:
        if action not in {"approve_positive", "accept_gap", "reject", "edit", "defer", "not_applies"}:
            raise ReviewError("invalid_action", "Unknown explicit reviewer action.")
        safe_id(request_id)
        if len(reason) > 8000 or (answer_text is not None and len(answer_text) > 20000):
            raise ReviewError("input_limit", "Decision text exceeds the configured limit.")
        payload = {"run_id": run_id, "row_id": row_id, "action": action, "actor": self.actor,
                   "expected_assessment_generation": expected_assessment_generation,
                   "expected_row_revision": expected_row_revision, "reason": reason, "answer_text": answer_text,
                   "human_validated": human_validated}
        payload_hash = digest("m3.decision-request.v1", payload)
        with self.transaction(run_id) as connection:
            if (connection.execute("SELECT 1 FROM inventory_promotions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM correspondence_events WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM coverage_events WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM revision_requests WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()):
                raise ReviewError("idempotency_conflict", "This request ID belongs to a requirement revision.")
            previous = connection.execute("SELECT payload_hash,event FROM decisions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise ReviewError("idempotency_conflict", "This decision request ID already has different content.")
                return json.loads(previous[1])
            snapshot = self._load(connection, run_id)
            row = self._row(snapshot, row_id)
            if row.get("retired"):
                raise ReviewError("row_retired", "This historical obligation was retired by an explicit revision; review its replacements.")
            if "decision_revision" in row and row["decision_revision"] != decision_revision(row):
                raise ReviewError("decision_revision_invalid", "Stored decision CAS is invalid; inspect/restore authoritative state.")
            if expected_assessment_generation != snapshot["assessment_generation"] or expected_row_revision != row.get("decision_revision", row["row_revision"]):
                raise ReviewError("stale_revision", "The row changed; inspect its fresh revision before deciding.",
                                  assessment_generation=snapshot["assessment_generation"], row_revision=row["row_revision"],
                                  decision_revision=row.get("decision_revision", row["row_revision"]))
            expected_content_revision = row["row_revision"]
            if action in {"reject", "not_applies"} and not reason.strip():
                raise ReviewError("reason_required", "This action requires a human rationale.")
            if answer_text is not None and action != "edit":
                raise ReviewError("invalid_transition", "Answer changes require a separate edit action.")
            if human_validated and action != "approve_positive":
                raise ReviewError("invalid_transition", "Human validation applies only to an explicit positive approval.")
            assessment = row.get("assessment", {})
            status = assessment.get("status", "needs_review")
            coverage_complete = self._coverage(connection, snapshot)["effective"]
            source_complete = coverage_complete["complete"] and not coverage_complete.get("artifact_issues")
            if action in {"approve_positive", "accept_gap"}:
                from .currentness import require_current
                require_current(snapshot, row, provider=self.policy_provider)
                self.validate_row(snapshot, row)
            if action == "approve_positive":
                validation = row.get("validation", {})
                if human_validated:
                    if row.get("unvalidated_note") or (isinstance(validation, dict) and validation.get("method") == "reviewer_edit"):
                        raise ReviewError("unvalidated_prose", "Reviewer-added prose needs a separate claim/citation validation workflow.")
                    row["validation"] = validation = {
                        "schema_version": 1, "state": "validated", "method": "explicit-human-assessment",
                        "provenance": self.actor, "reason": "Reviewer explicitly attested the exact source-bound restricted answer.",
                    }
                validated = validation in {"validated", "valid"} if isinstance(validation, str) else validation.get("state") in {"validated", "valid"}
                applicability = row["requirement"].get("applicability", "unknown")
                if (status != "supported" or not validated or assessment.get("counterevidence")
                        or not assessment.get("support") or not assessment.get("complete", False)
                        or not source_complete or not snapshot["corpus"].get("complete", False)
                        or applicability not in {"applies", "applicable"}):
                    raise ReviewError("approval_blocked", "Positive approval needs fully supported, validated, applicable content with no conflict.")
                row["approval"] = "approved_positive"
                row["row_revision"] = row_revision(row)
            elif action == "accept_gap":
                if (status not in {"cannot_answer_absent", "cannot_answer_conflicting"} or not assessment.get("complete", False)
                        or not source_complete or not snapshot["corpus"].get("complete", False)
                        or (status == "cannot_answer_conflicting" and not assessment.get("counterevidence"))):
                    raise ReviewError("approval_blocked", "Gap acknowledgement requires a valid complete absent/conflicting assessment.")
                row["approval"] = "approved_gap"
            elif action == "reject":
                row["approval"] = "rejected"
            elif action == "edit":
                if answer_text is None:
                    raise ReviewError("answer_required", "Supply the new text for an edit.")
                # Reviewer prose is explicitly unvalidated and never silently approved.
                row["unvalidated_note"] = answer_text
                row["answer_text"] = answer_text
                row["validation"] = {"state": "unvalidated", "method": "reviewer_edit"}
                row["approval"] = "pending"
                row["row_revision"] = row_revision(row)
                row["invalidation_reason"] = "interpretation_changed"
            elif action == "not_applies":
                row["requirement"]["applicability"] = "not_applies"
                row["approval"] = "pending"
                row["row_revision"] = row_revision(row)
            else:
                row["approval"] = "pending"
            if action in {"edit", "not_applies"}:
                requirement_id = row["requirement"]["requirement_id"]
                related = set(row["requirement"].get("child_ids", []))
                related.add(row["requirement"].get("parent_id"))
                for other in snapshot["rows"]:
                    req = other["requirement"]
                    if other is not row and (req["requirement_id"] in related or req.get("parent_id") == requirement_id):
                        other["previous_decision_event_id"] = other.get("decision_event_id")
                        other["approval"] = "stale"
                        other["invalidation_reason"] = "interpretation_changed"
                        other["dependency_revision"] = digest("m3.ancestry-change.v1", [row_id, row["row_revision"], request_id])
                        other["row_revision"] = row_revision(other)
            event_id = "D-" + digest("m3.decision-event.v1", [run_id, request_id, payload_hash])[:32]
            row["row_revision"] = row_revision(row)
            previous_event_id = row.get("decision_event_id")
            row["decision_event_id"] = event_id
            row["decision_revision"] = decision_revision(row)
            event = {"schema_version": 1, "event_id": event_id, "run_id": run_id, "row_id": row_id,
                     "request_id": request_id, "actor": self.actor, "action": action, "reason": reason,
                     "assessment_generation": snapshot["assessment_generation"], "row_revision": row["row_revision"],
                     "expected_row_revision": expected_content_revision,
                     "expected_decision_revision": expected_row_revision, "decision_revision": row["decision_revision"],
                     "previous_event_id": previous_event_id, "created_at": utc_now(),
                     "approval": row["approval"], "payload_hash": payload_hash}
            event["human_validated"] = human_validated
            row["decision_event_id"] = event_id
            self._save(connection, snapshot, "decision:" + action)
            event["matrix_generation"] = snapshot["matrix_generation"]
            connection.execute("INSERT INTO decisions(event_id,run_id,row_id,request_id,payload_hash,event) VALUES(?,?,?,?,?,?)",
                               (event_id, run_id, row_id, request_id, payload_hash, canonical(event)))
            connection.execute("INSERT INTO resume_intents(event_id,run_id) VALUES(?,?)", (event_id, run_id))
            return event

    def revise_rows(self, run_id: str, old_row_ids: list[str], replacement_rows: list[dict], *,
                    expected_matrix_generation: str, request_id: str, reason: str, intent: dict | None = None) -> dict:
        """Version interpretation/applicability/split/merge without a corpus edit.

        Producers construct and reassess replacement wires; this service alone
        retires/replaces active rows and invalidates related approval revisions.
        Complete prior snapshots and decisions remain append-only in SQLite.
        """
        safe_id(request_id)
        if not reason.strip() or len(reason) > 8000:
            raise ReviewError("reason_required", "Requirement revisions need a bounded human rationale.")
        if (not old_row_ids or len(old_row_ids) > 2000 or len(old_row_ids) != len(set(old_row_ids))
                or not replacement_rows or len(replacement_rows) > 2000):
            raise ReviewError("invalid_revision", "Select existing distinct rows and at least one replacement.")
        for row_id in old_row_ids:
            safe_id(row_id)
        payload = {"run_id": run_id, "old_row_ids": sorted(old_row_ids), "replacement_rows": replacement_rows,
                   "expected_matrix_generation": expected_matrix_generation, "reason": reason, "actor": self.actor, "intent": intent}
        payload_hash = digest("m3.requirement-revision-request.v1", payload)
        with self.transaction(run_id) as connection:
            if (connection.execute("SELECT 1 FROM inventory_promotions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM correspondence_events WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM coverage_events WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
                    or connection.execute("SELECT 1 FROM decisions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()):
                raise ReviewError("idempotency_conflict", "This request ID belongs to a row decision.")
            prior = connection.execute("SELECT payload_hash,snapshot FROM revision_requests WHERE run_id=? AND request_id=?",
                                       (run_id, request_id)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise ReviewError("idempotency_conflict", "Revision request ID already has different content.")
                return json.loads(prior["snapshot"])
            snapshot = self._load(connection, run_id)
            if expected_matrix_generation != snapshot["matrix_generation"]:
                raise ReviewError("stale_generation", "Requirement revision used an outdated matrix generation.", matrix_generation=snapshot["matrix_generation"])
            old_rows = [self._row(snapshot, row_id) for row_id in old_row_ids]
            from .currentness import require_current
            for selected in old_rows:
                require_current(snapshot, selected, provider=self.policy_provider)
            if any(row.get("retired") for row in old_rows):
                raise ReviewError("row_retired", "Retired rows cannot be revised again as active obligations.")
            old_ids = set(old_row_ids)
            existing_ids = {row["row_id"] for row in snapshot["rows"]}
            replacements = []
            new_ids: set[str] = set()
            for supplied in replacement_rows:
                row = deepcopy(supplied)
                row["requirement"] = validate_record("requirement", row["requirement"])
                row["assessment"] = validate_record("assessment", row["assessment"])
                row_id = safe_id(row["row_id"])
                if row_id in new_ids or (row_id in existing_ids and row_id not in old_ids):
                    raise ReviewError("duplicate_row", "Replacement IDs must be unique and cannot overwrite unselected rows.")
                if (row["assessment"]["corpus_generation"] != _hash_corpus(snapshot["corpus"])
                        or row["assessment"]["requirement_revision"] != row["requirement"]["revision"]):
                    raise ReviewError("stale_assessment", "Replacement assessment must bind its exact requirement and current corpus.")
                new_ids.add(row_id)
                row.update(assessment_generation=snapshot["assessment_generation"], approval="pending",
                           decision_event_id=None, retired=False, revision_ancestry=sorted(old_ids))
                row.setdefault("unvalidated_note", "")
                row["validation"] = deepcopy(row["assessment"]["validation"])
                row["previous_decision_event_ids"] = [old.get("decision_event_id") for old in old_rows if old.get("decision_event_id")]
                row["row_revision"] = row_revision(row)
                self.validate_row(snapshot, row)
                replacements.append(row)
            affected_requirements = {row["requirement"]["requirement_id"] for row in old_rows + replacements}
            related_requirements = set(affected_requirements)
            for row in old_rows + replacements:
                related_requirements.update(row["requirement"].get("child_ids", []))
                if row["requirement"].get("parent_id"):
                    related_requirements.add(row["requirement"]["parent_id"])
            result_rows = []
            for row in snapshot["rows"]:
                if row["row_id"] in old_ids:
                    if row["row_id"] in new_ids:
                        continue
                    row.update(retired=True, approval="stale", invalidation_reason="requirement_revised",
                               previous_decision_event_id=row.get("decision_event_id"), decision_event_id=None,
                               replacement_row_ids=sorted(new_ids))
                    row["row_revision"] = row_revision(row)
                elif (row["requirement"]["requirement_id"] in related_requirements
                      or row["requirement"].get("parent_id") in affected_requirements):
                    row.update(approval="stale", invalidation_reason="interpretation_changed",
                               previous_decision_event_id=row.get("decision_event_id"), decision_event_id=None)
                    row["dependency_revision"] = payload_hash
                    row["row_revision"] = row_revision(row)
                result_rows.append(row)
            snapshot["rows"] = result_rows + replacements
            if len(snapshot["rows"]) > 2000:
                raise ReviewError("row_limit", "Revision would exceed the complete inventory limit.")
            event = {"schema_version": 1, "event_id": "V-" + digest("m3.requirement-revision.v1", [run_id, request_id, payload_hash])[:32],
                     "request_id": request_id, "actor": self.actor, "reason": reason, "created_at": utc_now(),
                     "assessment_generation": snapshot["assessment_generation"], "old_row_ids": sorted(old_ids),
                     "replacement_row_ids": sorted(new_ids), "payload_hash": payload_hash}
            snapshot.setdefault("requirement_revision_events", []).append(event)
            self._save(connection, snapshot, "requirement_revision")
            connection.execute("INSERT INTO revision_requests VALUES(?,?,?,?,?,?)",
                               (run_id, request_id, payload_hash, canonical(event), canonical(snapshot), canonical(payload)))
            return snapshot

    def revision_request(self, run_id: str, request_id: str) -> dict | None:
        """Read original intent/result before rebuilding replacements on a retry."""
        safe_id(request_id)
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            row = connection.execute("SELECT payload,event,snapshot FROM revision_requests WHERE run_id=? AND request_id=?",
                                     (run_id, request_id)).fetchone()
            return {key: json.loads(row[key]) for key in ("payload", "event", "snapshot")} if row else None

    def update_run(self, run_id: str, artifact: dict, corpus: dict, rows: list[dict], *,
                   expected_matrix_generation: str, reason: str = "new_corpus_generation",
                   inventory_report: dict | None = None, provider_config_sha256: str | None = None) -> dict:
        with self.transaction(run_id) as connection:
            previous = self._load(connection, run_id)
            if previous["matrix_generation"] != expected_matrix_generation:
                raise ReviewError("stale_generation", "Source update used an outdated matrix generation.", matrix_generation=previous["matrix_generation"])
            policy = deepcopy(previous["policy"])
            policy["provider_config_sha256"] = provider_config_sha256
            snapshot = self._prepare(run_id, artifact, corpus, rows, policy, inventory_report)
            if snapshot["assessment_generation"] == previous["assessment_generation"]:
                raise ReviewError("unchanged_assessment", "Use explicit row decisions for unchanged source and corpus.")
            from rfp_matrix.evidence import describe_changes
            snapshot["change_report"] = describe_changes(previous["artifact"], artifact, previous["corpus"], corpus)
            snapshot["change_record"] = {"schema_version": 1, "actor": self.actor, "reason": reason,
                                         "created_at": utc_now(), "previous_matrix_generation": previous["matrix_generation"],
                                         "context_sha": snapshot["change_report"]["context_sha"],
                                         "assessment_policy_changed": previous["policy"] != policy,
                                         "previous_provider_config_sha256": previous["policy"].get("provider_config_sha256"),
                                         "provider_config_sha256": provider_config_sha256,
                                         "approval_policy": "all_rows_stale_no_top_k_retention"}
            old = {row["row_id"]: row for row in previous["rows"]}
            for row in snapshot["rows"]:
                prior = old.get(row["row_id"])
                if prior:
                    row["previous_decision_event_id"] = prior.get("decision_event_id")
                    row["previous_row_revision"] = prior["row_revision"]
                row["approval"] = "stale"
                row["invalidation_reason"] = reason
            # Removed obligations stay visible until explicitly dispositioned.
            new_ids = {row["row_id"] for row in snapshot["rows"]}
            for row_id, prior in old.items():
                if row_id not in new_ids:
                    retired = deepcopy(prior)
                    retired.update(retired=True, approval="stale", invalidation_reason="source_requirement_removed",
                                   previous_decision_event_id=prior.get("decision_event_id"), decision_event_id=None,
                                   assessment_generation=snapshot["assessment_generation"])
                    retired["assessment"]["status"] = "needs_review"
                    retired["row_revision"] = row_revision(retired)
                    snapshot["rows"].append(retired)
            snapshot["created_at"] = previous["created_at"]
            if len(snapshot["rows"]) > 2000:
                raise ReviewError("row_limit", "Source update plus preserved retired obligations exceeds the complete row bound.")
            snapshot["requirement_revision_events"] = previous.get("requirement_revision_events", [])
            if _hash_artifact(artifact) == _hash_artifact(previous["artifact"]) and "coverage_revision" in previous:
                snapshot["coverage"] = previous["coverage"]
                snapshot["coverage_revision"] = previous["coverage_revision"]
            self._save(connection, snapshot, "source_update:" + reason)
            return snapshot

    def graph_epoch(self, run_id: str) -> int:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0])

    def recover_graph_epoch(self, run_id: str, reason: str, *, reset_intents: bool = False) -> int:
        with self.transaction(run_id) as connection:
            self._load(connection, run_id)
            epoch = int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0]) + 1
            connection.execute("INSERT INTO graph_epochs VALUES(?,?,?,?)", (run_id, epoch, reason, utc_now()))
            if reset_intents:
                connection.execute("UPDATE resume_intents SET state='pending',detail=? WHERE run_id=?", (reason, run_id))
            return epoch

    def presentation(self, run_id: str, row_id: str) -> dict:
        with self.transaction(run_id) as connection:
            snapshot = self._load(connection, run_id)
            row = self._row(snapshot, row_id)
            epoch = int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0])
            request_id = "P-" + digest("m3.presentation.v1", [run_id, row_id, row["row_revision"], epoch, row.get("decision_event_id")])[:32]
            existed = connection.execute("SELECT 1 FROM presentations WHERE request_id=?", (request_id,)).fetchone() is not None
            connection.execute("INSERT OR IGNORE INTO presentations VALUES(?,?,?,?,?,?,?)",
                               (request_id, run_id, row_id, snapshot["assessment_generation"], row["row_revision"], epoch, utc_now()))
            return {"request_id": request_id, "run_id": run_id, "row_id": row_id,
                    "assessment_generation": snapshot["assessment_generation"], "row_revision": row["row_revision"], "epoch": epoch,
                    "decision_revision": row.get("decision_revision", row["row_revision"]),
                    "persisted_before": existed}

    def pending_intents(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT d.event FROM decisions d JOIN resume_intents i USING(event_id) WHERE i.run_id=? AND i.state='pending' ORDER BY d.seq", (run_id,))]

    def applied(self, event_id: str) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute("SELECT 1 FROM applied_events a JOIN decisions d ON d.event_id=a.event_id WHERE a.event_id=? AND a.epoch=(SELECT MAX(epoch) FROM graph_epochs WHERE run_id=d.run_id)", (event_id,)).fetchone() is not None

    def mark_applied(self, run_id: str, event_id: str, epoch: int, detail: str = "resumed") -> None:
        with self.transaction(run_id) as connection:
            event = connection.execute("SELECT 1 FROM decisions WHERE run_id=? AND event_id=?", (run_id, event_id)).fetchone()
            if not event:
                raise ReviewError("event_missing", "Resume cannot apply an uncommitted decision.")
            connection.execute("INSERT INTO applied_events VALUES(?,?,?) ON CONFLICT(event_id) DO UPDATE SET applied_at=excluded.applied_at,epoch=excluded.epoch", (event_id, utc_now(), epoch))
            connection.execute("UPDATE resume_intents SET state='applied',detail=? WHERE run_id=? AND event_id=?", (detail, run_id, event_id))

    def event(self, run_id: str, event_id: str) -> dict:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT event FROM decisions WHERE run_id=? AND event_id=?", (run_id, event_id)).fetchone()
            if not row:
                raise ReviewError("event_missing", "No committed decision event with this ID.")
            return json.loads(row[0])

    def event_presentation(self, run_id: str, event: dict, epoch: int | None = None) -> dict:
        """Find the durable pre-decision presentation, or rebuild one in a new epoch."""
        with self.transaction(run_id) as connection:
            self._load(connection, run_id)
            if epoch is None:
                record = connection.execute(
                    "SELECT * FROM presentations WHERE request_id=? AND run_id=? AND row_id=? AND assessment_generation=? AND row_revision=? ORDER BY epoch DESC LIMIT 1",
                    (event["request_id"], run_id, event["row_id"], event["assessment_generation"], event["expected_row_revision"]),
                ).fetchone()
                if record:
                    return {**{key: record[key] for key in ("request_id", "run_id", "row_id", "assessment_generation", "row_revision", "epoch")},
                            "decision_revision": event.get("expected_decision_revision", event["expected_row_revision"])}
                epoch = int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0])
            request_id = "P-" + digest("m3.presentation-rebuild.v1", [run_id, event["row_id"], event["expected_row_revision"], epoch, event["request_id"]])[:32]
            connection.execute("INSERT OR IGNORE INTO presentations VALUES(?,?,?,?,?,?,?)",
                               (request_id, run_id, event["row_id"], event["assessment_generation"], event["expected_row_revision"], epoch, utc_now()))
            return {"request_id": request_id, "run_id": run_id, "row_id": event["row_id"],
                    "assessment_generation": event["assessment_generation"], "row_revision": event["expected_row_revision"], "epoch": epoch,
                    "decision_revision": event.get("expected_decision_revision", event["expected_row_revision"])}

    def reconcile(self, run_id: str, driver: Any = None) -> dict:
        policy = self.policy_status(run_id)["policy_status"]
        pending = self.pending_intents(run_id)
        if driver is None:
            return {"run_id": run_id, "status": "checkpoint_unavailable", "pending": len(pending), "applied": 0, "policy_status": policy}
        count = 0
        with self.run_lock(run_id):
            for event in self.pending_intents(run_id):
                driver.apply(self, run_id, event)
                count += 1
        return {"run_id": run_id, "status": "reconciled", "pending": len(self.pending_intents(run_id)), "applied": count, "policy_status": policy}
