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
    return digest("m3.row-revision.v1", {key: row.get(key) for key in (
        "assessment_generation", "requirement", "assessment", "answer_text", "validation", "unvalidated_note",
        "dependency_revision", "retired", "revision_ancestry",
    )})


def matrix_generation(snapshot: dict) -> str:
    return digest("m3.matrix-generation.v1", {
        "assessment_generation": snapshot["assessment_generation"],
        "rows": [{"row_id": row["row_id"], "row_revision": row["row_revision"],
                  "approval": row["approval"], "decision_event_id": row.get("decision_event_id")}
                 for row in sorted(snapshot["rows"], key=lambda value: value["row_id"])],
    })


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
"""


class ReviewStore:
    """One local-reviewer store. Actor identity is derived here, never from a row."""

    def __init__(self, state_dir: str | Path):
        supplied = Path(state_dir).expanduser()
        if supplied.is_symlink():
            raise ReviewError("unsafe_path", "State directory must not be a symlink.")
        self.root = supplied.resolve()
        if "onedrive" in {part.lower() for part in self.root.parts}:
            raise ReviewError("unsafe_state_location", "Use machine-local state outside OneDrive.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "review.sqlite3"
        if self.db_path.is_symlink():
            raise ReviewError("unsafe_path", "Domain database must not be a symlink.")
        self._mutex = threading.RLock()
        self._lock_depth: dict[str, int] = {}
        new_database = not self.db_path.exists()
        marker = self.root / "store-id.txt"
        if marker.is_symlink():
            raise ReviewError("unsafe_path", "Domain identity marker must not be a symlink.")
        if new_database and marker.exists():
            raise ReviewError("domain_missing", "Domain database is missing; restore it rather than rebuilding from checkpoints.")
        try:
            with closing(self._connect()) as connection:
                if new_database:
                    connection.executescript(_SCHEMA)
                    connection.execute("INSERT INTO meta VALUES('schema_version', '1')")
                    connection.execute("INSERT INTO meta VALUES('store_id', ?)", (str(uuid.uuid4()),))
                else:
                    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    if not row or row[0] != "1":
                        raise ReviewError("unsupported_schema", "Unknown domain schema; refusing automatic rebuild.")
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ReviewError("corrupt_domain", "Domain database integrity check failed.")
                self.store_id = connection.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise ReviewError("corrupt_domain", "Cannot read authoritative domain database.") from exc
        self.actor = "local:" + getpass.getuser() + ":" + self.store_id
        if marker.exists() and marker.read_text(encoding="utf-8") != self.store_id:
            raise ReviewError("domain_identity_mismatch", "Database and local store identity do not match.")
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(self.store_id)
                handle.flush()
                os.fsync(handle.fileno())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=0, isolation_level=None)
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
        with self.run_lock(run_id):
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

    def events(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return [json.loads(row[0]) for row in connection.execute("SELECT event FROM decisions WHERE run_id=? ORDER BY seq", (run_id,))]

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
            if connection.execute("SELECT 1 FROM revision_requests WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone():
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
            if expected_assessment_generation != snapshot["assessment_generation"] or expected_row_revision != row["row_revision"]:
                raise ReviewError("stale_revision", "The row changed; inspect its fresh revision before deciding.",
                                  assessment_generation=snapshot["assessment_generation"], row_revision=row["row_revision"])
            if action in {"reject", "not_applies"} and not reason.strip():
                raise ReviewError("reason_required", "This action requires a human rationale.")
            if answer_text is not None and action != "edit":
                raise ReviewError("invalid_transition", "Answer changes require a separate edit action.")
            if human_validated and action != "approve_positive":
                raise ReviewError("invalid_transition", "Human validation applies only to an explicit positive approval.")
            assessment = row.get("assessment", {})
            status = assessment.get("status", "needs_review")
            if action in {"approve_positive", "accept_gap"}:
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
                        or not snapshot["artifact"].get("complete", False) or not snapshot["corpus"].get("complete", False)
                        or applicability not in {"applies", "applicable"}):
                    raise ReviewError("approval_blocked", "Positive approval needs fully supported, validated, applicable content with no conflict.")
                row["approval"] = "approved_positive"
                row["row_revision"] = row_revision(row)
            elif action == "accept_gap":
                if (status not in {"cannot_answer_absent", "cannot_answer_conflicting"} or not assessment.get("complete", False)
                        or not snapshot["artifact"].get("complete", False) or not snapshot["corpus"].get("complete", False)
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
            event = {"schema_version": 1, "event_id": event_id, "run_id": run_id, "row_id": row_id,
                     "request_id": request_id, "actor": self.actor, "action": action, "reason": reason,
                     "assessment_generation": snapshot["assessment_generation"], "row_revision": row["row_revision"],
                     "expected_row_revision": expected_row_revision,
                     "previous_event_id": row.get("decision_event_id"), "created_at": utc_now(),
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
            if connection.execute("SELECT 1 FROM decisions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone():
                raise ReviewError("idempotency_conflict", "This request ID belongs to a row decision.")
            prior = connection.execute("SELECT payload_hash,snapshot FROM revision_requests WHERE run_id=? AND request_id=?",
                                       (run_id, request_id)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise ReviewError("idempotency_conflict", "Revision request ID already has different content.")
                return json.loads(prior["snapshot"])
            snapshot = self._load(connection, run_id)
            if expected_matrix_generation != snapshot["matrix_generation"]:
                raise ReviewError("stale_generation", "Requirement revision used an outdated matrix generation.")
            old_rows = [self._row(snapshot, row_id) for row_id in old_row_ids]
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
                   inventory_report: dict | None = None) -> dict:
        with self.transaction(run_id) as connection:
            previous = self._load(connection, run_id)
            if previous["matrix_generation"] != expected_matrix_generation:
                raise ReviewError("stale_generation", "Source update used an outdated matrix generation.")
            snapshot = self._prepare(run_id, artifact, corpus, rows, previous["policy"], inventory_report)
            if snapshot["assessment_generation"] == previous["assessment_generation"]:
                raise ReviewError("unchanged_assessment", "Use explicit row decisions for unchanged source and corpus.")
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
            snapshot["requirement_revision_events"] = previous.get("requirement_revision_events", [])
            self._save(connection, snapshot, "source_update:" + reason)
            return snapshot

    def graph_epoch(self, run_id: str) -> int:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0])

    def recover_graph_epoch(self, run_id: str, reason: str) -> int:
        with self.transaction(run_id) as connection:
            self._load(connection, run_id)
            epoch = int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0]) + 1
            connection.execute("INSERT INTO graph_epochs VALUES(?,?,?,?)", (run_id, epoch, reason, utc_now()))
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
                    "persisted_before": existed}

    def pending_intents(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            self._load(connection, run_id)
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT d.event FROM decisions d JOIN resume_intents i USING(event_id) WHERE i.run_id=? AND i.state='pending' ORDER BY d.seq", (run_id,))]

    def applied(self, event_id: str) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute("SELECT 1 FROM applied_events WHERE event_id=?", (event_id,)).fetchone() is not None

    def mark_applied(self, run_id: str, event_id: str, epoch: int, detail: str = "resumed") -> None:
        with self.transaction(run_id) as connection:
            event = connection.execute("SELECT 1 FROM decisions WHERE run_id=? AND event_id=?", (run_id, event_id)).fetchone()
            if not event:
                raise ReviewError("event_missing", "Resume cannot apply an uncommitted decision.")
            connection.execute("INSERT OR IGNORE INTO applied_events VALUES(?,?,?)", (event_id, utc_now(), epoch))
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
                    return {key: record[key] for key in ("request_id", "run_id", "row_id", "assessment_generation", "row_revision", "epoch")}
                epoch = int(connection.execute("SELECT MAX(epoch) FROM graph_epochs WHERE run_id=?", (run_id,)).fetchone()[0])
            request_id = "P-" + digest("m3.presentation-rebuild.v1", [run_id, event["row_id"], event["expected_row_revision"], epoch, event["request_id"]])[:32]
            connection.execute("INSERT OR IGNORE INTO presentations VALUES(?,?,?,?,?,?,?)",
                               (request_id, run_id, event["row_id"], event["assessment_generation"], event["expected_row_revision"], epoch, utc_now()))
            return {"request_id": request_id, "run_id": run_id, "row_id": event["row_id"],
                    "assessment_generation": event["assessment_generation"], "row_revision": event["expected_row_revision"], "epoch": epoch}

    def reconcile(self, run_id: str, driver: Any = None) -> dict:
        pending = self.pending_intents(run_id)
        if driver is None:
            return {"run_id": run_id, "status": "checkpoint_unavailable", "pending": len(pending), "applied": 0}
        count = 0
        with self.run_lock(run_id):
            for event in self.pending_intents(run_id):
                driver.apply(self, run_id, event)
                count += 1
        return {"run_id": run_id, "status": "reconciled", "pending": len(self.pending_intents(run_id)), "applied": count}
