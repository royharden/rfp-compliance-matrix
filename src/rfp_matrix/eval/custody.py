"""External custody integration contracts and pure irreversible seal transitions.

No private labels, files or author-readable custody store are implemented here.
Authority, atomic spent storage and scorer ports must run outside author/method
processes. Injected implementations cannot certify their own independence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol

from .runner import Authorization, FrozenComparison, fingerprint, require_digest, sha


@dataclass(frozen=True)
class VerifiedGrant:
    """Trusted authority result, never accepted directly from a method request."""

    principal_sha: str
    role: str
    freeze_sha: str
    input_sha: str
    split_sha: str
    labels_sha: str
    family_set_sha: str
    seal_sha: str
    authorization_id_sha: str
    disclose_failure_ids: bool = False


class ExternalAuthority(Protocol):
    def verify(self, *, manifest: str, seal: bytes) -> VerifiedGrant: ...


class SpentStore(Protocol):
    def consume_once(self, *, grant: VerifiedGrant) -> None:
        """Atomically reject spent/overlapping families/raw inputs and consume.

        Must be durable and serialized across authorizations/processes. A
        different seal or candidate does not make spent families eligible again.
        """
        ...


class PrivateScorer(Protocol):
    def score(self, *, authorization: Authorization, method: str,
              predictions: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]: ...


def consume_seal_state(state: Mapping[str, Any], *, grant: VerifiedGrant,
                       expected_revision: str) -> dict[str, Any]:
    """Pure transition for the external atomic store; sealed -> spent only.

    Caller must transact CAS and the cross-seal raw/family overlap index together.
    A proposed result does not establish that consumption persisted. No reset,
    retry-to-unspent, or new-seal exception exists, including after failures.
    """
    fields = {"schema_version", "status", "freeze_sha", "input_sha", "split_sha", "labels_sha",
              "family_set_sha", "seal_sha", "principal_sha", "revision"}
    if set(state) != fields or type(state["schema_version"]) is not int or state["schema_version"] != 1 or state["status"] != "sealed":
        raise ValueError("Seal is invalid, already spent or unsupported")
    body = {key: value for key, value in state.items() if key != "revision"}
    if fingerprint(body) != state["revision"] or state["revision"] != expected_revision:
        raise ValueError("Stale or modified seal state")
    if grant.role != "independent_custodian" or type(grant.disclose_failure_ids) is not bool:
        raise ValueError("External independent custodian grant required")
    for key in fields - {"schema_version", "status", "revision"}:
        require_digest(state[key])
        if state[key] != getattr(grant, key):
            raise ValueError("Grant differs from prospective sealed state")
    require_digest(grant.authorization_id_sha)
    next_state = {**body, "status": "spent", "authorization_id_sha": grant.authorization_id_sha,
                  "previous_revision": state["revision"]}
    return {**next_state, "revision": fingerprint(next_state)}


class ExternalCustodianAdapter:
    """Wire external authority/spent/scorer services; construction has no effects."""

    def __init__(self, authority: ExternalAuthority, spent_store: SpentStore, scorer: PrivateScorer) -> None:
        self.authority, self.spent_store, self.scorer = authority, spent_store, scorer
        self._consumed: dict[str, Authorization] = {}

    def authorize_and_consume(self, *, frozen_manifest: str, seal: bytes) -> Authorization:
        manifest = json.loads(frozen_manifest)
        freeze = FrozenComparison(frozen_manifest, sha(frozen_manifest.encode("utf-8")))
        grant = self.authority.verify(manifest=frozen_manifest, seal=seal)
        for field in ("principal_sha", "freeze_sha", "input_sha", "split_sha", "labels_sha",
                      "family_set_sha", "seal_sha", "authorization_id_sha"):
            require_digest(getattr(grant, field))
        if (grant.role != "independent_custodian" or grant.freeze_sha != freeze.digest
                or grant.input_sha != manifest["input_sha"] or grant.split_sha != manifest["pins"]["split"]
                or grant.labels_sha != manifest["pins"]["labels"] or grant.seal_sha != sha(seal)
                or type(grant.disclose_failure_ids) is not bool):
            raise ValueError("External grant does not bind the frozen prospective candidate")
        # The authority verifies grouping rather than trusting caller family names.
        # The store enforces overlap and spend across candidate hashes/seal IDs.
        self.spent_store.consume_once(grant=grant)
        authorization = Authorization(grant.freeze_sha, grant.input_sha, grant.split_sha, grant.labels_sha,
            grant.seal_sha, grant.authorization_id_sha, grant.disclose_failure_ids)
        self._consumed[authorization.authorization_id_sha] = authorization
        return authorization

    def score(self, *, authorization: Authorization, method: str,
              predictions: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]:
        if method not in ("B0a", "B0b", "F1", "F2") or self._consumed.get(authorization.authorization_id_sha) != authorization:
            raise ValueError("Scoring requires this adapter's consumed authorization")
        return self.scorer.score(authorization=authorization, method=method, predictions=predictions)
