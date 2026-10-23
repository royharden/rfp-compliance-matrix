"""Versioned application wires and content identity; no I/O or provider imports."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
NORMALIZATION_POLICY = "m3-casefold-whitespace-alignment-v1"
INVENTORY_POLICY = "m3-structural-modal-v1"
ASSESSMENT_POLICY = "m3-registered-sentences-conservative-v1"


class M3Error(ValueError):
    """A user-visible failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(domain: str, value: Any) -> str:
    return hashlib.sha256((domain + "\x00" + canonical_json(value)).encode("utf-8")).hexdigest()


def raw_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1


class SourceRecord(Wire):
    source_id: str = Field(min_length=1, max_length=160)
    raw_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    terms: str = Field(min_length=1, max_length=4000)
    source_url: str | None = Field(default=None, max_length=2000)
    retrieved_at: str | None = None
    fictional: bool = False
    rights_status: Literal["verified", "pending", "excluded"] = "pending"
    media_type: str = Field(max_length=100)


class TextAnchor(Wire):
    source_id: str = Field(min_length=1, max_length=160)
    source_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_index: int = Field(ge=0, lt=100)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=200000)
    excerpt_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_id: str | None = None
    normalization_policy: str = NORMALIZATION_POLICY

    @model_validator(mode="after")
    def ordered(self) -> "TextAnchor":
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("anchor must address its exact original Unicode quote")
        if raw_sha256(self.quote.encode("utf-8")) != self.excerpt_sha:
            raise ValueError("excerpt hash does not match quote")
        return self


class Section(Wire):
    section_id: str
    title: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class PageArtifact(Wire):
    page_index: int = Field(ge=0, lt=100)
    text: str = Field(max_length=2_000_000)
    text_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: Literal["ok", "sparse", "empty", "unsupported"]
    issues: list[str] = Field(default_factory=list, max_length=100)
    sections: list[Section] = Field(default_factory=list, max_length=2000)
    tables: list[list[list[str | None]]] = Field(default_factory=list, max_length=100)
    normalized_text: str = Field(max_length=2_000_000)
    alignment: list[list[int]] = Field(max_length=2_000_000)


class DocumentArtifact(Wire):
    source: SourceRecord
    artifact_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser: str
    parser_version: str
    policy_hash: str
    normalization_policy: str = NORMALIZATION_POLICY
    pages: list[PageArtifact] = Field(min_length=1, max_length=100)
    complete: bool
    issues: list[str] = Field(default_factory=list, max_length=1000)


class RequirementVersion(Wire):
    requirement_id: str
    revision: str
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list, max_length=2000)
    anchors: list[TextAnchor] = Field(min_length=1, max_length=100)
    literal_clause: str = Field(min_length=1, max_length=200000)
    interpretation: str = Field(min_length=1, max_length=200000)
    mandatory: bool
    conditional: bool
    administrative: bool
    applicability: Literal["applies", "unknown", "not_applies"] = "applies"
    condition: str | None = None
    uncertainty: list[str] = Field(default_factory=list, max_length=100)
    policy_hash: str
    method: str


class EvidenceRecord(Wire):
    record_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=200000)
    raw_sha: str
    artifact_sha: str
    terms: str = Field(min_length=1, max_length=4000)
    fictional: Literal[True] = True
    source_url: str | None = None


class EvidenceCorpus(Wire):
    generation: str
    records: list[EvidenceRecord] = Field(max_length=200)
    complete: bool = True
    fictional: Literal[True] = True
    issues: list[str] = Field(default_factory=list, max_length=1000)
    policy_hash: str


class EvidencePointer(Wire):
    record_id: str
    corpus_generation: str
    source_sha: str
    artifact_sha: str
    sentence_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=200000)
    excerpt_sha: str
    broad_fallback: bool = False


class Validation(Wire):
    state: Literal["validated", "pending", "invalid"]
    method: str
    provenance: str
    reason: str


class EvidenceAssessment(Wire):
    requirement_id: str
    requirement_revision: str
    corpus_generation: str
    status: Literal["supported", "partial", "cannot_answer_absent", "cannot_answer_conflicting", "needs_review"]
    support: list[EvidencePointer] = Field(default_factory=list, max_length=100)
    counterevidence: list[EvidencePointer] = Field(default_factory=list, max_length=100)
    candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    complete: bool
    reason: str
    method: str
    origin: Literal["baseline", "mock", "replay", "live", "unavailable"]
    model: str | None = None
    policy_hash: str
    judgments: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    usage: dict[str, Any] | None = None
    validation: Validation


T = TypeVar("T", bound=Wire)


def validate_record(record_type: type[T] | str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a wire without mutating it; returns its canonical JSON mapping."""
    kinds: dict[str, type[Wire]] = {
        "source": SourceRecord, "artifact": DocumentArtifact, "anchor": TextAnchor,
        "requirement": RequirementVersion, "corpus": EvidenceCorpus,
        "evidence_pointer": EvidencePointer, "assessment": EvidenceAssessment,
    }
    cls = kinds[record_type] if isinstance(record_type, str) else record_type
    try:
        return cls.model_validate(dict(value)).model_dump(mode="json")
    except (ValueError, TypeError) as exc:
        raise M3Error("invalid_wire", f"Invalid {cls.__name__}", detail=str(exc)) from exc


__all__ = ["M3Error", "canonical_json", "digest", "raw_sha256", "validate_record", "TextAnchor", "DocumentArtifact", "RequirementVersion", "EvidenceCorpus", "EvidenceAssessment"]
