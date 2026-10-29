"""Trusted, run-scoped read-only policy bindings; no judgment/admission port."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Sequence

from .store import ReviewError, safe_id


@dataclass(frozen=True)
class ReviewPolicyBinding:
    config: Any
    identity: Any


class ReviewPolicyBindings:
    """Constructor stores supplied objects only, without any metadata collection."""

    def __init__(self, bindings: Sequence[ReviewPolicyBinding]):
        from rfp_matrix.providers import ProviderConfig, is_review_policy_identity, policy_identity_projection
        if not isinstance(bindings, (list, tuple)) or not 1 <= len(bindings) <= 32:
            raise ReviewError("review_policy_limit", "Supply 1..32 explicit read-only policy bindings.")
        entries = {}
        for binding in bindings:
            if (type(binding) is not ReviewPolicyBinding or not isinstance(binding.config, ProviderConfig)
                    or not is_review_policy_identity(binding.identity)):
                raise ReviewError("review_policy_identity_required", "Bindings require the provider-owned inert nominal review identity, never a judgment port.")
            if (binding.identity.status == "available"
                    and dict(binding.identity.identity) != policy_identity_projection(binding.config)):
                raise ReviewError("review_policy_identity_required", "Supplied read-only handle and scoped configuration must describe the same exact policy.")
            key = (binding.config.document_id, binding.config.method)
            if key in entries:
                raise ReviewError("duplicate_review_policy", "Only one current read-only policy per owned run/method is allowed.")
            entries[key] = binding
        self._entries = MappingProxyType(entries)

    def resolve(self, snapshot: dict, method: str):
        """No fallback between documents, methods, source or corpus generations."""
        binding = self._entries.get((snapshot["run_id"], method))
        if binding is None:
            return None
        config = binding.config
        if (config.document_id != snapshot["run_id"] or config.source_sha256 != snapshot["artifact"]["artifact_sha"]
                or config.corpus_sha256 != snapshot["corpus"]["generation"] or config.method != method):
            raise ReviewError("review_policy_scope_mismatch", "Explicit read-only policy no longer matches the run/source/corpus/method; refresh its nonsecret configuration.")
        return binding.identity


def bind_review_policies(store, configurations: Sequence[Any], *, run_ids: Sequence[str]) -> ReviewPolicyBindings:
    """Explicit command operation, never a constructor or request-selected path.

    The provider owner validates actual current descriptor/config metadata and
    creates an inert identity. This operation never constructs an inference
    factory, client, tape, ledger, approval, SDK or provider credential lookup.
    """
    from rfp_matrix.providers import ProviderConfig, review_policy_identity
    from rfp_matrix.source import policy_context
    if (not isinstance(run_ids, (list, tuple)) or not 1 <= len(run_ids) <= 200
            or any(not isinstance(run_id, str) for run_id in run_ids)
            or len(set(run_ids)) != len(run_ids)):
        raise ReviewError("invalid_run_grant", "Read-only policy binding requires distinct explicitly owned run IDs.")
    if not isinstance(configurations, (list, tuple)) or not 1 <= len(configurations) <= 32:
        raise ReviewError("review_policy_limit", "Supply 1..32 nonsecret read-only configurations.")
    for run_id in run_ids:
        safe_id(run_id)
    snapshots = {run_id: store.load_run(run_id) for run_id in run_ids}
    seen = set()
    for config in configurations:
        if not isinstance(config, ProviderConfig) or config.document_id not in snapshots:
            raise ReviewError("review_policy_scope_mismatch", "Read-only policy must name an explicitly granted run.")
        config.validate()
        if config.method == "B0a":
            raise ReviewError("review_policy_method", "B0a is keyless and needs no typed review-policy configuration.")
        snapshot = snapshots[config.document_id]
        active_methods = {row["assessment"]["method"] for row in snapshot["rows"] if not row.get("retired")}
        if (config.source_sha256 != snapshot["artifact"]["artifact_sha"]
                or config.corpus_sha256 != snapshot["corpus"]["generation"]
                or (active_methods and config.method not in active_methods)):
            raise ReviewError("review_policy_scope_mismatch", "Read-only policy requires this run's actual source/corpus and an existing active assessment method.")
        key = (config.document_id, config.method)
        if key in seen:
            raise ReviewError("duplicate_review_policy", "Duplicate run/method review-policy binding.")
        seen.add(key)
    bindings = []
    for config in configurations:
        current = policy_context(config.method)
        identity = review_policy_identity(config, semantic_provenance=current.get("descriptor"))
        bindings.append(ReviewPolicyBinding(config, identity))
    result = ReviewPolicyBindings(bindings)
    store.policy_provider = result  # Apply only after every scope/config succeeds.
    return result
