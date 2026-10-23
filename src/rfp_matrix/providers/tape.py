"""Bounded, hash-checked offline records. Integrity never proves authenticity."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, canonical_json, digest
from .config import ProviderConfig
from .live import validate_questions
from .ports import unavailable, validate_judgment

MAX_TAPE_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 256
IDENTITY_FIELDS = {"schema_version", "method", "state_sha256", "questions_sha256", "candidate_sha256",
    "question_shapes", "model", "sdk_name", "sdk_version", "provenance_sha256", "question_policy",
    "source_policy", "source_sha256", "corpus_sha256", "request_sha256"}
RESULT_FIELDS = {"schema_version", "status", "origin", "code", "model", "sdk_version", "answers",
    "raw_classifications", "usage", "actual_cost_usd", "simulated_cost_usd", "cost_status",
    "distribution_semantics"}


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise M3Error("replay_schema", "Replay object has missing or unexpected fields")
    return dict(value)


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise M3Error("replay_identity", "Replay requires exact source/policy hashes")
    return value


def replay_identity(state: Mapping[str, Any], questions: Mapping[str, Any], *,
                    config: ProviderConfig) -> dict[str, Any]:
    """Caller supplies code-owned questions and exact artifact/corpus bindings.

    State and full question contents are hashed, never stored in the tape.
    Disabled policy is allowed; no live admission/budget or secret is needed.
    """
    config.validate()
    validate_questions(questions)
    _hash(config.source_sha256)
    _hash(config.corpus_sha256)
    shapes = {key: {"type": question["type"], "criteria": list(question["criteria"]) if question["type"] == "choice" else []}
              for key, question in questions.items()}
    value = {"schema_version": 1, "method": config.method,
        "state_sha256": digest("m3.replay-state.v1", dict(state)),
        "questions_sha256": digest("m3.replay-questions.v1", dict(questions)),
        "candidate_sha256": digest("m3.replay-candidates.v1", shapes), "question_shapes": shapes,
        "model": config.model, "sdk_name": config.sdk_name, "sdk_version": config.sdk_version,
        "provenance_sha256": digest("m3.replay-provenance.v1", {"model": config.model_provenance,
            "sdk": config.sdk_provenance, "price_policy": config.price_policy,
            "price_provenance": config.price_provenance}),
        "question_policy": config.question_policy, "source_policy": config.source_policy,
        "source_sha256": config.source_sha256, "corpus_sha256": config.corpus_sha256}
    value["request_sha256"] = digest("m3.portable-replay-request.v1", value)
    return _identity(value)


def _identity(value: Any) -> dict[str, Any]:
    result = _object(value, IDENTITY_FIELDS)
    if type(result["schema_version"]) is not int or result["schema_version"] != 1 or result["method"] not in {"B0b", "F1", "F2"}:
        raise M3Error("replay_schema", "Unsupported replay identity version/method")
    for name in ["state_sha256", "questions_sha256", "candidate_sha256", "provenance_sha256",
                 "source_sha256", "corpus_sha256", "request_sha256"]:
        _hash(result[name])
    for name in ["model", "sdk_name", "sdk_version", "question_policy", "source_policy"]:
        if not isinstance(result[name], str) or not 1 <= len(result[name]) <= 200:
            raise M3Error("replay_identity", "Invalid model/SDK/policy identity")
    shapes = result["question_shapes"]
    if not isinstance(shapes, dict) or not 1 <= len(shapes) <= 16:
        raise M3Error("replay_schema", "Invalid replay question shapes")
    for key, shape in shapes.items():
        _object(shape, {"type", "criteria"})
        options = shape["criteria"]
        if not isinstance(key, str) or not 1 <= len(key) <= 160 or shape["type"] not in {"noul", "choice"} or not isinstance(options, list):
            raise M3Error("replay_schema", "Invalid replay question/candidate IDs")
        if any(not isinstance(option, str) or not 1 <= len(option) <= 160 for option in options) or len(set(options)) != len(options):
            raise M3Error("replay_schema", "Invalid/duplicate replay option")
        if (shape["type"] == "noul" and options) or (shape["type"] == "choice" and not 1 <= len(options) <= 255):
            raise M3Error("replay_schema", "Invalid closed replay criteria")
    if result["candidate_sha256"] != digest("m3.replay-candidates.v1", shapes):
        raise M3Error("replay_integrity", "Replay candidate digest mismatch")
    body = {k: v for k, v in result.items() if k != "request_sha256"}
    if result["request_sha256"] != digest("m3.portable-replay-request.v1", body):
        raise M3Error("replay_integrity", "Replay request digest mismatch")
    return json.loads(canonical_json(result))


def _money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        amount = Decimal(value) if isinstance(value, str) else Decimal("NaN")
    except InvalidOperation as exc:
        raise M3Error("replay_cost", "Replay cost must be decimal text or unknown") from exc
    if not amount.is_finite() or amount < 0:
        raise M3Error("replay_cost", "Replay cost must be finite and nonnegative")
    return str(amount)


def _safe_result(value: Mapping[str, Any], identity: Mapping[str, Any], kind: str) -> dict[str, Any]:
    expected_origin = {"captured_live": "live", "authored_mock": "mock"}.get(kind)
    if expected_origin is None or value.get("origin") != expected_origin:
        raise M3Error("replay_origin", "Replay cannot be relabelled as captured live or authored mock")
    status = value.get("status")
    if status not in {"ok", "needs_review", "unavailable"}:
        raise M3Error("replay_schema", "Unsupported recorded status")
    model = value.get("model")
    sdk = value.get("sdk_version")
    observed = status == "ok" or value.get("raw_classifications") is not None
    if kind == "captured_live" and observed and (model != identity["model"] or sdk != identity["sdk_version"]):
        raise M3Error("replay_identity", "Captured model/SDK must match request identity")
    if kind == "captured_live" and not observed:
        # Failed attempts do not establish the model/version actually observed.
        model, sdk = None, None
    if kind == "authored_mock" and (model is not None or sdk is not None):
        raise M3Error("replay_origin", "Authored mock cannot assert an observed live model/SDK")
    shapes = identity["question_shapes"]
    questions = {key: {"type": shape["type"], "criteria": {option: None for option in shape["criteria"]}}
                 for key, shape in shapes.items()}
    answers = {}
    if status == "ok":
        validated = validate_judgment(value, questions)
        for key, answer in validated["answers"].items():
            allowed = {"type", "noul"} if answer["type"] == "noul" else {"type", "choice", "confidence", "probabilities"}
            answers[key] = {name: answer[name] for name in allowed}
    elif value.get("answers") not in (None, {}):
        raise M3Error("replay_schema", "Unsuccessful records cannot contain approved probability answers")
    labels = value.get("raw_classifications")
    if labels is not None:
        if identity["method"] != "F2" or not isinstance(labels, dict) or set(labels) != set(shapes):
            raise M3Error("replay_schema", "Invalid F2 raw classification IDs")
        for key, label in labels.items():
            shape = shapes[key]
            if shape["type"] == "noul":
                if label is not None and type(label) is not bool:
                    raise M3Error("replay_schema", "F2 yes/no is boolean or unknown")
            elif not isinstance(label, str) or label not in shape["criteria"]:
                raise M3Error("replay_schema", "F2 choice must be registered")
    if identity["method"] == "F2" and status == "ok":
        raise M3Error("replay_schema", "F2 generated classification cannot assert Jev probability answers")
    usage = value.get("usage")
    if usage is not None:
        if not isinstance(usage, Mapping):
            raise M3Error("replay_schema", "Invalid recorded usage")
        usage = {name: usage.get(name) for name in ["input_tokens", "output_tokens"]}
        if any(count is not None and (type(count) is not int or count < 0) for count in usage.values()):
            raise M3Error("replay_schema", "Invalid recorded token counts")
    code = value.get("code") or ("recorded_success" if status == "ok" else "provider_unavailable")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,160}", code):
        raise M3Error("replay_schema", "Recorded code must be a stable code, not raw error text")
    actual, simulated = _money(value.get("actual_cost_usd")), _money(value.get("simulated_cost_usd"))
    if kind == "authored_mock" and actual is not None:
        raise M3Error("replay_origin", "Mock cannot assert an observed charge")
    semantics = "generated-closed-labels-no-probabilities" if identity["method"] == "F2" else "provider-reported-typesafe" if kind == "captured_live" else "authored-mock-distribution"
    return {"schema_version": 1, "status": status, "origin": expected_origin, "code": code,
        "model": model, "sdk_version": sdk, "answers": answers,
        "raw_classifications": labels, "usage": usage, "actual_cost_usd": actual,
        "simulated_cost_usd": simulated, "cost_status": "known" if actual is not None else "unknown",
        "distribution_semantics": semantics}


def make_replay_record(identity: Mapping[str, Any], result: Mapping[str, Any], *, record_kind: str) -> dict[str, Any]:
    """Sanitize captured results, excluding raw responses, headers and extra fields.

    Caller-authored kind is a claim, not authentication. Input must be live/mock;
    a replayed result cannot be recaptured and promoted to a new live observation.
    """
    identity = _identity(identity)
    value = {"schema_version": 1, "record_kind": record_kind, "identity": identity,
             "result": _safe_result(result, identity, record_kind)}
    value["record_sha256"] = digest("m3.replay-record.v1", value)
    if len(canonical_json(value).encode("utf-8")) > MAX_TAPE_BYTES:
        raise M3Error("replay_limit", "Replay record exceeds tape limit")
    return json.loads(canonical_json(value))


def _record(value: Any) -> dict[str, Any]:
    record = _object(value, {"schema_version", "record_kind", "identity", "result", "record_sha256"})
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise M3Error("replay_schema", "Unsupported record version")
    _object(record["result"], RESULT_FIELDS)
    checked = make_replay_record(record["identity"], record["result"], record_kind=record["record_kind"])
    if checked != record:
        raise M3Error("replay_integrity", "Replay record hash or sanitized fields differ")
    return checked


def _records(records: Any) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise M3Error("replay_limit", "A tape needs 1..256 records")
    if len(canonical_json(records).encode("utf-8")) > MAX_TAPE_BYTES:
        raise M3Error("replay_limit", "Replay records exceed 2 MiB")
    checked = [_record(record) for record in records]
    keys = [record["identity"]["request_sha256"] for record in checked]
    if len(set(keys)) != len(keys):
        raise M3Error("replay_conflict", "Duplicate request identities in replay tape")
    return sorted(checked, key=lambda record: record["identity"]["request_sha256"])


def _path(path: str | Path, *, writing: bool) -> Path:
    raw = str(path)
    target = Path(path).absolute()
    if raw.startswith(("\\\\", "//")) or ".." in Path(path).parts or not target.name.endswith(".m3-replay.json") or ":" in target.name:
        raise M3Error("replay_path", "Use a local .m3-replay.json path without traversal or streams")
    for part in [target, *target.parents]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise M3Error("replay_path", "Replay paths cannot contain links/junctions")
    if not target.parent.is_dir() or (not writing and not target.is_file()):
        raise M3Error("replay_path", "Replay parent/file is absent or not a regular file")
    return target


def write_replay_tape(path: str | Path, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    checked = _records(records)
    value = {"schema_version": 1, "kind": "m3-replay-tape", "records": checked}
    value["tape_sha256"] = digest("m3.replay-tape.v1", value)
    data = (canonical_json(value) + "\n").encode("utf-8")
    if len(data) > MAX_TAPE_BYTES:
        raise M3Error("replay_limit", "Replay tape exceeds 2 MiB")
    target = _path(path, writing=True)
    # Exclusive creation never overwrites a prior tape; malformed partial writes
    # remain visibly invalid rather than being silently repaired or promoted.
    with target.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(target), "tape_sha256": value["tape_sha256"], "records": len(checked),
            "authenticity": "not_verified", "bytes": len(data)}


class FileReplayProvider:
    def __init__(self, records: list[Mapping[str, Any]], *, config: ProviderConfig) -> None:
        self.config = config
        self._records = {r["identity"]["request_sha256"]: r for r in _records(records)}

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> dict[str, Any]:
        try:
            identity = replay_identity(state, questions, config=self.config)
            record = self._records.get(identity["request_sha256"])
            if record is None:
                return unavailable("replay_request_not_found", model=self.config.model)
            checked = _record(record)
            result = dict(checked["result"])
            result.update(origin="replay", original_origin=checked["result"]["origin"],
                record_kind=checked["record_kind"], request_hash=identity["request_sha256"],
                record_sha256=checked["record_sha256"], incremental_cost_usd="0",
                original_observed_cost_usd=checked["result"]["actual_cost_usd"],
                authenticity="not_verified", integrity="hash_checked",
                question_policy=identity["question_policy"], source_policy=identity["source_policy"],
                requested_model=identity["model"])
            return validate_judgment(result, questions)
        except (M3Error, ValueError, TypeError):
            return unavailable("replay_incompatible", model=self.config.model)


def load_replay_tape(path: str | Path, *, config: ProviderConfig) -> FileReplayProvider:
    """Explicit local read, never a provider call or credential lookup."""
    target = _path(path, writing=False)
    before = target.stat()
    if before.st_size > MAX_TAPE_BYTES:
        raise M3Error("replay_limit", "Replay tape exceeds 2 MiB")
    with target.open("rb") as stream:
        data = stream.read(MAX_TAPE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_TAPE_BYTES or before.st_ino != after.st_ino or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise M3Error("replay_changed", "Tape changed during bounded read")
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise M3Error("replay_schema", "Duplicate JSON keys are forbidden")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
        value = _object(value, {"schema_version", "kind", "records", "tape_sha256"})
        if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "m3-replay-tape":
            raise M3Error("replay_schema", "Unsupported replay tape")
        body = {k: v for k, v in value.items() if k != "tape_sha256"}
        if _hash(value["tape_sha256"]) != digest("m3.replay-tape.v1", body):
            raise M3Error("replay_integrity", "Replay tape digest mismatch")
        return FileReplayProvider(value["records"], config=config)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, M3Error):
            raise
        raise M3Error("replay_schema", "Malformed replay JSON") from exc
