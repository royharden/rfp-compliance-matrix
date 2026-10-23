"""Explicit, serializable admission policy. Construction performs no I/O."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, digest

SDK_VERSION = "0.7.5"
SDK_WHEEL_SHA256 = "967a8b61494d536abb55ee3f3a2b10e81685cb6baad6ccdcab588bdc6c9b86d2"
F2_MODEL = "gpt-4.1-mini-2025-04-14"


@dataclass(frozen=True)
class ProviderConfig:
    method: str = "F1"
    enabled: bool = False
    model: str = "jev-1.13.0"
    model_provenance: str = ""
    sdk_name: str = "typesafe-sdk"
    sdk_version: str = SDK_VERSION
    sdk_provenance: str = ""
    license_reference: str = ""
    terms_reference: str = ""
    price_policy: str = ""
    price_provenance: str = ""
    input_per_million_usd: str | None = None
    output_per_million_usd: str | None = None
    budget_usd: str = "0"
    max_input_tokens: int = 8000
    max_output_tokens: int = 1024
    deadline_seconds: float = 60.0
    token_bound_policy: str = ""
    question_policy: str = "m3-typed-registered-candidates-v1"
    source_policy: str = "m3-source-artifact-and-corpus-state-v1"
    document_id: str = ""
    source_sha256: str = ""
    corpus_sha256: str = ""
    document_budget_usd: str = "1"
    admission_reference: str = ""
    approved_config_sha256: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderConfig":
        if not isinstance(value, Mapping) or set(value) - {f.name for f in fields(cls)}:
            raise M3Error("provider_config", "Unknown provider configuration fields; secrets are not configuration")
        result = cls(**dict(value))
        result.validate()
        return result

    def identity(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("admission_reference")
        value.pop("approved_config_sha256")
        return value

    @property
    def config_sha256(self) -> str:
        return digest("m3.provider-config.v1", self.identity())

    def validate(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"enabled", "max_input_tokens", "max_output_tokens", "deadline_seconds"}:
                continue
            if field.name in {"input_per_million_usd", "output_per_million_usd"} and value is None:
                continue
            if not isinstance(value, str) or len(value) > 4000:
                raise M3Error("provider_config", "Policy fields must be bounded strings")
        if self.method not in {"B0b", "F1", "F2"} or type(self.enabled) is not bool:
            raise M3Error("provider_config", "Unknown method or non-boolean enabled gate")
        if type(self.max_input_tokens) is not int or not 0 < self.max_input_tokens <= 8000:
            raise M3Error("provider_config", "Input cap must be an integer in 1..8000")
        if type(self.max_output_tokens) is not int or not 0 < self.max_output_tokens <= 8000:
            raise M3Error("provider_config", "Output reservation cap must be in 1..8000")
        if isinstance(self.deadline_seconds, bool) or not isinstance(self.deadline_seconds, (int, float)) or not 0 < self.deadline_seconds <= 60:
            raise M3Error("provider_config", "Deadline must be finite and at most 60 seconds")
        for value in [self.budget_usd, self.document_budget_usd, self.input_per_million_usd, self.output_per_million_usd]:
            if value is None:
                continue
            try:
                amount = Decimal(value)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise M3Error("provider_config", "Invalid decimal budget/price") from exc
            if not amount.is_finite() or amount < 0:
                raise M3Error("provider_config", "Budget/price must be finite and nonnegative")
        if self.method in {"B0b", "F1"}:
            if not re.fullmatch(r"jev-\d+\.\d+\.\d+", self.model):
                raise M3Error("provider_config", "An exact Jev model version is required")
            if self.sdk_name != "typesafe-sdk" or self.sdk_version != SDK_VERSION:
                raise M3Error("provider_config", "Only the inspected SDK version is supported by this adapter")
        elif (not re.fullmatch(r"gpt-[A-Za-z0-9.-]+-\d{4}-\d{2}-\d{2}", self.model)
              or self.sdk_name != "python-stdlib-urllib"):
            raise M3Error("provider_config", "F2 requires an explicit dated snapshot and stdlib transport identity")

    def admission_reason(self) -> str | None:
        self.validate()
        if not self.enabled or Decimal(self.budget_usd) <= 0:
            return "disabled_zero_budget"
        if Decimal(self.budget_usd) > 10 or not 0 < Decimal(self.document_budget_usd) <= 1:
            return "budget_scope_limit"
        if not self.document_id or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in [self.source_sha256, self.corpus_sha256]):
            return "source_corpus_binding_missing"
        if self.token_bound_policy != "utf8-byte-upper-bound-plus-1024-v1":
            return "token_bound_policy_unadmitted"
        if any(not value for value in [self.model_provenance, self.sdk_provenance, self.license_reference, self.terms_reference,
               self.price_policy, self.price_provenance, self.token_bound_policy,
               self.admission_reference, self.question_policy, self.source_policy]):
            return "admission_provenance_missing"
        if self.input_per_million_usd is None or self.output_per_million_usd is None:
            return "price_unknown"
        if self.approved_config_sha256 != self.config_sha256:
            return "configuration_not_approved"
        return None
