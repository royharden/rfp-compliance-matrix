"""Source identity is separate from parsing identity; all spans use original text.

PDF parsing occurs in a bounded child with network disabled. Nothing in this module
fetches source URLs, executes PDF actions or silently OCRs image-only content.
"""
from __future__ import annotations

import importlib.metadata
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import socket
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from rfp_matrix.contracts import M3Error, NORMALIZATION_POLICY, canonical_json, digest, raw_sha256, validate_record


@dataclass(frozen=True)
class ParsePolicy:
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 100
    max_text_codepoints: int = 2_000_000
    sparse_floor: int = 20
    timeout_seconds: int = 45
    cpu_seconds: int = 30
    memory_bytes: int = 768 * 1024 * 1024


def _policy(policy: ParsePolicy | Mapping[str, Any] | None) -> ParsePolicy:
    result = policy if isinstance(policy, ParsePolicy) else ParsePolicy(**dict(policy or {}))
    if not (0 < result.max_bytes <= 20 * 1024 * 1024 and 0 < result.max_pages <= 100):
        raise M3Error("invalid_policy", "PDF policy cannot exceed application caps")
    if not (0 < result.max_text_codepoints <= 2_000_000 and 0 < result.timeout_seconds <= 60):
        raise M3Error("invalid_policy", "Invalid parser text or timeout cap")
    if not (0 < result.cpu_seconds <= 45 and 0 < result.memory_bytes <= 1024**3):
        raise M3Error("invalid_policy", "Invalid parser CPU or memory cap")
    return result


def normalize_with_alignment(text: str) -> tuple[str, list[list[int]]]:
    """Casefold/collapse whitespace; each normalized codepoint maps to [a,b)."""
    characters: list[str] = []
    intervals: list[list[int]] = []
    pending_space: list[int] | None = None
    for index, character in enumerate(text):
        if character.isspace():
            if characters:
                pending_space = [pending_space[0], index + 1] if pending_space else [index, index + 1]
            continue
        if pending_space:
            characters.append(" ")
            intervals.append(pending_space)
            pending_space = None
        for expanded in character.casefold():
            characters.append(expanded)
            intervals.append([index, index + 1])
    return "".join(characters), intervals


def _sections(text: str, page_index: int) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^(?:\d+(?:\.\d+)*[.)]?\s+[^\n]{2,140}|[A-Z][A-Z /&-]{3,100})$", text))
    result = []
    for index, match in enumerate(headings):
        result.append({"schema_version": 1, "section_id": f"p{page_index}-s{index}",
                       "title": match.group(), "start": match.start(),
                       "end": headings[index + 1].start() if index + 1 < len(headings) else len(text)})
    return result


def _page(text: str, index: int, policy: ParsePolicy, *, issues: list[str] | None = None,
          tables: list[list[list[str | None]]] | None = None) -> dict[str, Any]:
    issues = list(issues or [])
    count = sum(not ch.isspace() for ch in text)
    coverage = "ok" if count >= policy.sparse_floor else ("sparse" if count else "empty")
    if coverage != "ok":
        issues.append("native_text_sparse" if count else "no_native_text")
    if "image_content_requires_inspection" in issues or "extraction_error" in issues:
        coverage = "unsupported"
    normalized, alignment = normalize_with_alignment(text)
    return {"schema_version": 1, "page_index": index, "text": text,
            "text_sha": raw_sha256(text.encode("utf-8")), "coverage": coverage,
            "issues": sorted(set(issues)), "sections": _sections(text, index), "tables": tables or [],
            "normalized_text": normalized, "alignment": alignment}


def _artifact(source: dict[str, Any], pages: list[dict[str, Any]], *, parser: str,
              parser_version: str, policy: ParsePolicy, issues: list[str] | None = None) -> dict[str, Any]:
    if not pages or len(pages) > policy.max_pages:
        raise M3Error("page_limit", "PDF must contain between one and the maximum permitted pages")
    if sum(len(page["text"]) for page in pages) > policy.max_text_codepoints:
        raise M3Error("text_limit", "Extracted text exceeds application limit; no partial artifact accepted")
    value = {"schema_version": 1, "source": source, "parser": parser,
             "parser_version": parser_version, "policy_hash": digest("m3.parse-policy.v1", asdict(policy)),
             "normalization_policy": NORMALIZATION_POLICY, "pages": pages,
             "complete": all(page["coverage"] == "ok" and not page["issues"] for page in pages),
             "issues": list(issues or [])}
    value["artifact_sha"] = digest("m3.document-artifact.v1", value)
    return validate_record("artifact", value)


def parse_text_pages(pages: Sequence[str], *, source_id: str, terms: str,
                     fictional: bool = True) -> dict[str, Any]:
    """Explicit synthetic/development input; never pretends to be a parsed PDF."""
    if isinstance(pages, (str, bytes)) or not all(isinstance(page, str) for page in pages):
        raise M3Error("invalid_text", "pages must be a sequence of original text strings")
    policy = ParsePolicy()
    text_pages = list(pages)
    raw = canonical_json(text_pages).encode("utf-8")
    if len(raw) > policy.max_bytes:
        raise M3Error("file_limit", "Synthetic source exceeds input limit")
    source = {"schema_version": 1, "source_id": source_id, "raw_sha": raw_sha256(raw), "terms": terms,
              "source_url": None, "retrieved_at": None, "fictional": fictional,
              "rights_status": "pending", "media_type": "application/x-m3-synthetic-pages"}
    return _artifact(source, [_page(text, index, policy) for index, text in enumerate(text_pages)],
                     parser="synthetic-text-pages", parser_version="1", policy=policy,
                     issues=["Synthetic development source; not public-PDF quality evidence"])


def _deny_network(*args: Any, **kwargs: Any) -> Any:
    raise OSError("Network disabled in PDF parser child")


def _resource_limits(policy: ParsePolicy) -> Any:
    """Fail closed if OS enforcement cannot be established; handle kept alive."""
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (policy.memory_bytes, policy.memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x00000002 | 0x00000100  # CPU time and per-process committed memory
    limits.basic.process_time = policy.cpu_seconds * 10_000_000
    limits.process_memory = policy.memory_bytes
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise OSError("Cannot establish PDF parser CPU/memory Job limits")
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise OSError("Cannot assign PDF parser to bounded Job")
    return handle


def _parse_worker(connection: Any, raw: bytes, policy_data: dict[str, Any]) -> None:
    # Everything fallible is inside the child and returns bounded plain JSON.
    try:
        policy = _policy(policy_data)
        limit_handle = _resource_limits(policy)
        socket.socket = _deny_network  # type: ignore[assignment]
        socket.create_connection = _deny_network
        socket.getaddrinfo = _deny_network
        import pdfplumber
        version = importlib.metadata.version("pdfplumber")
        pages: list[dict[str, Any]] = []
        with pdfplumber.open(io.BytesIO(raw)) as document:
            if len(document.pages) > policy.max_pages:
                raise M3Error("page_limit", "PDF exceeds page cap")
            total = 0
            for index, page in enumerate(document.pages):
                issues: list[str] = []
                tables: list[list[list[str | None]]] = []
                try:
                    text = page.extract_text(layout=False) or ""
                    if page.images:
                        issues.append("image_content_requires_inspection")
                    if "\ufffd" in text or "\x00" in text:
                        issues.append("broken_text_layer")
                    extracted_tables = page.extract_tables() or []
                    if extracted_tables:
                        tables = extracted_tables[:100]
                        issues.append("table_reading_order_requires_inspection")
                    # Reading order heuristic is deliberately a warning, not certification.
                    if any(len(re.findall(r"\S\s{4,}\S", line)) > 1 for line in text.splitlines()):
                        issues.append("possible_columns_require_inspection")
                except Exception:
                    text = ""
                    issues.append("extraction_error")
                total += len(text)
                if total > policy.max_text_codepoints:
                    raise M3Error("text_limit", "Extracted text exceeds cap")
                pages.append(_page(text, index, policy, issues=issues, tables=tables))
        message = {"ok": True, "pages": pages, "version": version}
        encoded = canonical_json(message).encode("utf-8")
        if len(encoded) > 64 * 1024 * 1024:
            raise M3Error("output_limit", "Parser output exceeds bounded IPC limit")
        connection.send_bytes(encoded)
        _ = limit_handle
    except Exception as exc:
        code = exc.code if isinstance(exc, M3Error) else "pdf_unsupported"
        connection.send_bytes(canonical_json({"ok": False, "code": code,
                                             "message": "PDF parser unavailable, encrypted, corrupt or unsupported"}).encode("utf-8"))
    finally:
        connection.close()


def parse_pdf(path: str | Path, *, source_id: str | None = None,
              terms: str = "user supplied; rights unverified", source_url: str | None = None,
              fictional: bool = False, policy: ParsePolicy | Mapping[str, Any] | None = None) -> dict[str, Any]:
    rules = _policy(policy)
    supplied = Path(path)
    if supplied.is_symlink() or not supplied.is_file():
        raise M3Error("invalid_path", "PDF must be a regular non-symlink file")
    if supplied.stat().st_size > rules.max_bytes:
        raise M3Error("file_limit", "PDF exceeds 20 MiB input cap")
    with supplied.open("rb") as stream:
        raw = stream.read(rules.max_bytes + 1)
    if len(raw) > rules.max_bytes or not raw.startswith(b"%PDF-"):
        raise M3Error("invalid_pdf", "PDF magic/length check failed")
    raw_hash = raw_sha256(raw)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    worker = context.Process(target=_parse_worker, args=(writer, raw, asdict(rules)), daemon=True)
    worker.start()
    writer.close()
    try:
        if not reader.poll(rules.timeout_seconds):
            raise M3Error("parser_timeout", "PDF parser exceeded bounded deadline")
        try:
            result = json.loads(reader.recv_bytes(64 * 1024 * 1024))
        except (EOFError, OSError, ValueError) as exc:
            raise M3Error("parser_failure", "PDF child failed or exceeded output limit") from exc
    finally:
        reader.close()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=1)
    if not result["ok"]:
        raise M3Error(result["code"], result["message"])
    source = {"schema_version": 1, "source_id": source_id or f"rfp-{raw_hash[:16]}",
              "raw_sha": raw_hash, "terms": terms, "source_url": source_url, "retrieved_at": None,
              "fictional": fictional, "rights_status": "pending", "media_type": "application/pdf"}
    return _artifact(source, result["pages"], parser="pdfplumber-native", parser_version=result["version"], policy=rules)


def _verify_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_record("artifact", artifact)
    content = dict(value)
    expected = content.pop("artifact_sha")
    if digest("m3.document-artifact.v1", content) != expected:
        raise M3Error("artifact_corrupt", "Extraction artifact identity does not match its content")
    for index, page in enumerate(value["pages"]):
        if page["page_index"] != index or raw_sha256(page["text"].encode("utf-8")) != page["text_sha"]:
            raise M3Error("artifact_corrupt", "Page index or original text digest mismatch")
        normalized, alignment = normalize_with_alignment(page["text"])
        if normalized != page["normalized_text"] or alignment != page["alignment"]:
            raise M3Error("artifact_corrupt", "Normalization alignment mismatch")
    coverage_complete = all(page["coverage"] == "ok" and not page["issues"] for page in value["pages"])
    if value["complete"] != coverage_complete:
        raise M3Error("artifact_corrupt", "Complete-coverage flag contradicts per-page inspection state")
    return value


def validate_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Validate wire, content identity, page digests and original alignment."""
    return _verify_artifact(artifact)


def make_anchor(artifact: Mapping[str, Any], page_index: int, start: int, end: int,
                *, section_id: str | None = None) -> dict[str, Any]:
    if not isinstance(page_index, int) or page_index < 0 or page_index >= len(artifact["pages"]):
        raise M3Error("invalid_anchor", "Unknown page index")
    text = artifact["pages"][page_index]["text"]
    if not 0 <= start < end <= len(text):
        raise M3Error("invalid_anchor", "Anchor exceeds original page text")
    quote = text[start:end]
    return validate_record("anchor", {"schema_version": 1, "source_id": artifact["source"]["source_id"],
        "source_sha": artifact["source"]["raw_sha"], "artifact_sha": artifact["artifact_sha"],
        "page_index": page_index, "start": start, "end": end, "quote": quote,
        "excerpt_sha": raw_sha256(quote.encode("utf-8")), "section_id": section_id,
        "normalization_policy": artifact["normalization_policy"]})


def resolve_anchor(artifact: Mapping[str, Any], anchor: Mapping[str, Any]) -> dict[str, Any]:
    source = _verify_artifact(artifact)
    pointer = validate_record("anchor", anchor)
    if (pointer["source_id"] != source["source"]["source_id"] or
        pointer["source_sha"] != source["source"]["raw_sha"] or
        pointer["artifact_sha"] != source["artifact_sha"] or
        pointer["normalization_policy"] != source["normalization_policy"]):
        raise M3Error("stale_anchor", "Anchor belongs to a different immutable extraction")
    page_index = pointer["page_index"]
    if page_index >= len(source["pages"]):
        raise M3Error("invalid_anchor", "Unknown page index")
    page = source["pages"][page_index]
    start, end = pointer["start"], pointer["end"]
    if end > len(page["text"]) or page["text"][start:end] != pointer["quote"]:
        raise M3Error("invalid_anchor", "Literal quote does not roundtrip at original coordinates")
    if pointer["section_id"] is not None and not any(s["section_id"] == pointer["section_id"] and s["start"] <= start < s["end"] for s in page["sections"]):
        raise M3Error("invalid_anchor", "Section does not contain the original span")
    return {"quote": pointer["quote"], "context": page["text"][max(0, start - 180):min(len(page["text"]), end + 180)],
            "page_index": page_index, "section_id": pointer["section_id"], "start": start, "end": end}


class SourceRegistry:
    """Content-addressed extraction JSON; no arbitrary source-id path access."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, identity: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise M3Error("invalid_artifact_id", "Artifact ID must be a SHA-256 digest")
        if self.root.is_symlink():
            raise M3Error("invalid_registry", "Registry root cannot be a symlink")
        result = self.root / f"{identity}.json"
        if result.is_symlink():
            raise M3Error("invalid_registry", "Artifact entry cannot be a symlink")
        return result

    def save(self, artifact: Mapping[str, Any], *, raw_path: str | Path | None = None) -> str:
        value = _verify_artifact(artifact)
        if self.root.is_symlink():
            raise M3Error("invalid_registry", "Registry root cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if raw_path is not None:
            supplied = Path(raw_path)
            if supplied.is_symlink() or not supplied.is_file() or supplied.stat().st_size > 20 * 1024 * 1024:
                raise M3Error("invalid_path", "Original PDF must be a bounded regular file")
            with supplied.open("rb") as stream:
                original = stream.read(20 * 1024 * 1024 + 1)
            if len(original) > 20 * 1024 * 1024 or raw_sha256(original) != value["source"]["raw_sha"] or not original.startswith(b"%PDF-"):
                raise M3Error("source_changed", "Original PDF changed since parsing")
            blob = self.root / f"{value['source']['raw_sha']}.pdf"
            if blob.is_symlink():
                raise M3Error("invalid_registry", "Original blob cannot be a symlink")
            try:
                with blob.open("xb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if raw_sha256(blob.read_bytes()) != value["source"]["raw_sha"]:
                    raise M3Error("source_corrupt", "Existing immutable original blob differs")
        path = self._path(value["artifact_sha"])
        data = canonical_json(value).encode("utf-8")
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != data:
                raise M3Error("artifact_collision", "Existing immutable artifact differs")
        return value["artifact_sha"]

    def load(self, artifact_sha: str) -> dict[str, Any]:
        path = self._path(artifact_sha)
        if not path.is_file():
            raise M3Error("missing_artifact", "Immutable extraction artifact is missing")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise M3Error("artifact_corrupt", "Artifact exceeds registry size cap")
        try:
            value = _verify_artifact(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            if isinstance(exc, M3Error):
                raise
            raise M3Error("artifact_corrupt", "Immutable artifact cannot be read") from exc
        if value["artifact_sha"] != artifact_sha:
            raise M3Error("artifact_corrupt", "Artifact filename does not match content")
        return value

    def resolve(self, anchor: Mapping[str, Any]) -> dict[str, Any]:
        return resolve_anchor(self.load(str(anchor.get("artifact_sha", ""))), anchor)
