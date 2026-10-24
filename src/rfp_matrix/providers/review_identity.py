"""Pure read-only current-policy handle. No inference or admission capability."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, canonical_json
from rfp_matrix.provenance import SEMANTIC_PROFILE, verify_fingerprint_descriptor
from .config import ProviderConfig
from .ports import QUESTION_POLICY

SOURCE_POLICY = "m3-source-artifact-and-corpus-state-v1"
REVIEW_POLICY_PURPOSE = "review-policy-identity-only-v1"
IDENTITY_FIELDS = ("method", "model", "sdk_name", "sdk_version", "question_policy", "source_policy",
                   "document_id", "source_sha256", "corpus_sha256", "semantic_fingerprint")


def policy_identity_projection(config: ProviderConfig | Mapping[str, Any]) -> dict[str, Any]:
    """Pure stable identity, usable by a genuine prediction receipt producer.

    Accepts actual execution config unchanged; ignores budget/enabled/admission
    fields without evaluating admission or claiming execution/currentness. Old
    absent receipts cannot be reconstructed as proof by calling this helper.
    """
    try:
        policy = config if type(config) is ProviderConfig else ProviderConfig.from_mapping(config)
        policy.validate()
        if (any(not 1 <= len(getattr(policy, name)) <= 200 for name in IDENTITY_FIELDS)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", policy.document_id)
                or any(not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (policy.source_sha256, policy.corpus_sha256, policy.semantic_fingerprint))
                or policy.question_policy != QUESTION_POLICY or policy.source_policy != SOURCE_POLICY):
            raise M3Error("review_policy_identity_invalid", "Exact fixed policy and source identity is required")
        return {name: getattr(policy, name) for name in IDENTITY_FIELDS}
    except (M3Error, TypeError, ValueError):
        raise M3Error("review_policy_identity_invalid", "Exact fixed policy and source identity is required") from None


@dataclass(frozen=True, slots=True, init=False)
class ReviewPolicyIdentity:
    """Trusted application input, not an assessment, prediction port or attestation.

    Obtain through review_policy_identity; consumers must explicitly distinguish
    this class from JudgmentPort and compare actual current context/run bindings.
    A supplied descriptor's integrity does not prove its freshness or authorship.
    """
    config: ProviderConfig | None
    status: str
    reason: str | None
    _descriptor_json: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Use review_policy_identity with an explicit current descriptor")

    @property
    def identity(self) -> Mapping[str, Any]:
        # Include no config admission, budgets, prices, free text or credentials.
        try:
            return MappingProxyType(policy_identity_projection(self.config)) if self.config is not None else MappingProxyType({})
        except M3Error:
            return MappingProxyType({})

    @property
    def semantic_provenance(self) -> dict[str, Any] | None:
        return json.loads(self._descriptor_json) if self._descriptor_json is not None else None

    @property
    def read_only_policy(self) -> bool:
        return True

    @property
    def purpose(self) -> str:
        return REVIEW_POLICY_PURPOSE

    @property
    def execution_verified(self) -> bool:
        return False

    @property
    def live_authorization_verified(self) -> bool:
        return False


def is_review_policy_identity(value: Any) -> bool:
    """Pure exact-type check, no duck typing or user attribute/property evaluation.

    Inference/evaluation must reject this type before consuming any execution
    seal. The purpose string alone is not a trusted type/authenticity check.
    """
    return type(value) is ReviewPolicyIdentity


def _handle(config: ProviderConfig | None, status: str, reason: str | None,
            descriptor: Mapping[str, Any] | None = None) -> ReviewPolicyIdentity:
    handle = object.__new__(ReviewPolicyIdentity)
    for name, value in {"config": config, "status": status, "reason": reason,
                        "_descriptor_json": canonical_json(dict(descriptor)) if descriptor is not None else None}.items():
        object.__setattr__(handle, name, value)
    return handle


def review_policy_identity(config: ProviderConfig | Mapping[str, Any], *,
                           semantic_provenance: Mapping[str, Any] | None) -> ReviewPolicyIdentity:
    """Pure validation of caller-supplied policy and actual current descriptor.

    Never collects, imports SDKs, reads keys/files/DB/tapes, consumes approvals or
    enables a provider. The explicit command owner collects current provenance
    and independently checks exact document/run/artifact/corpus/assessment pins.
    A hash/config/handle alone cannot make old predictions or approvals current.
    """
    try:
        policy = config if type(config) is ProviderConfig else ProviderConfig.from_mapping(config)
        policy.validate()
    except (M3Error, TypeError, ValueError):
        return _handle(None, "unavailable", "review_policy_invalid_config")
    if (policy.enabled or Decimal(policy.budget_usd) != 0
            or policy.admission_reference or policy.approved_config_sha256):
        return _handle(policy, "unavailable", "review_policy_live_authority_forbidden")
    if (not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", policy.document_id)
            or any(not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in (policy.source_sha256, policy.corpus_sha256))):
        return _handle(policy, "unavailable", "review_policy_source_corpus_binding_missing")
    if policy.question_policy != QUESTION_POLICY or policy.source_policy != SOURCE_POLICY:
        return _handle(policy, "unavailable", "review_policy_fixed_policy_mismatch")
    if not policy.semantic_fingerprint:
        return _handle(policy, "unavailable", "review_policy_semantic_binding_missing")
    if not verify_fingerprint_descriptor(semantic_provenance, profile=SEMANTIC_PROFILE):
        return _handle(policy, "unavailable", "review_policy_descriptor_missing_or_invalid")
    # Defensive canonical copy: caller mutation cannot change the validated handle.
    descriptor = json.loads(canonical_json(dict(semantic_provenance)))
    if descriptor["identity"]["method"] != policy.method:
        return _handle(policy, "unavailable", "review_policy_method_mismatch", descriptor)
    if descriptor["restart_required"]:
        return _handle(policy, "restart_required", "review_policy_restart_required", descriptor)
    if descriptor["status"] != "available":
        return _handle(policy, "unavailable", "review_policy_source_unavailable", descriptor)
    if descriptor["fingerprint"] != policy.semantic_fingerprint:
        return _handle(policy, "unavailable", "review_policy_semantic_mismatch", descriptor)
    if policy.method == "F2":
        matches = descriptor["identity"]["runtime"]["python_version"] == policy.sdk_version
    else:
        sdk = descriptor["identity"]["components"]["judge"]["dependencies"]["typesafe-sdk"]
        matches = sdk["status"] == "available" and sdk["version"] == policy.sdk_version
    if not matches:
        return _handle(policy, "unavailable", "review_policy_sdk_metadata_missing_or_mismatch", descriptor)
    return _handle(policy, "available", None, descriptor)
