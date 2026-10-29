"""Shared UNRUN eval fakes, exposed only through the `eval_kit` fixture.

pytest loads this conftest under any --import-mode, so test modules never import
sibling test files by bare name. Fakes are not real custody, gold or provenance.
"""
import json
from types import SimpleNamespace

import pytest

from rfp_matrix.contracts import digest
from rfp_matrix.eval.runner import Authorization, PublicInput, PublicSpan, fingerprint, freeze_comparison, sha
from rfp_matrix.provenance import CANONICAL_POLICY, GROUPS, LIBRARIES, RENDER_PROFILE, SEMANTIC_PROFILE


def setup_comparison():
    source = PublicSpan("S", "source", sha(b"artifact"), 0, 0, 4, "must")
    evidence = PublicSpan("E", "evidence", sha(b"vendor"), 0, 0, 3, "yes", "vendor-record")
    public = PublicInput("public-doc", "family-a", b"%PDF-synthetic-protocol-bytes",
                         b'{"fictional":true}', sha(b"rights-fixture"), "verified", (source, evidence))
    pins = {k: sha(k.encode()) for k in ("source", "parser", "normalizer", "questions", "thresholds", "split", "labels", "scorer", "budget", "price")}
    configs = {m: {"config_sha": sha(m.encode()), "model_snapshot": "fixture-exact",
                  "sdk_snapshot": "fixture-sdk-exact", "price_sha": pins["price"],
                  "budget_sha": pins["budget"], "origin": "baseline" if m == "B0a" else "mock"}
               for m in ("B0a", "B0b", "F1", "F2")}
    frozen = freeze_comparison((public,), split="dev", pins=pins, method_configs=configs,
                               dataset_counts={"documents": 1, "families": 1})
    return public, frozen


class CustodianFake:
    """In-memory fixture only; it authenticates no actual principal/independence."""

    def __init__(self, frozen, *, disclose=False):
        self.frozen, self.disclose, self.spent = frozen, disclose, False
        self.scored = []

    def authorize_and_consume(self, *, frozen_manifest, seal):
        if self.spent or seal != b"external-test-seal" or frozen_manifest != self.frozen.canonical_manifest:
            raise ValueError("Fixture denial")
        self.spent = True
        manifest = json.loads(frozen_manifest)
        return Authorization(self.frozen.digest, manifest["input_sha"], manifest["pins"]["split"],
            manifest["pins"]["labels"], sha(seal), sha(b"fixture-authorization"), self.disclose)

    def score(self, *, authorization, method, predictions):
        self.scored.append(method)
        return {"population_sha": fingerprint({"input_sha": authorization.input_sha,
                "labels_sha": authorization.labels_sha, "split_sha": authorization.split_sha}),
                "inventory_recall": {"numerator": 0, "denominator": 1},
                "failure_ids": ["case-a"], "mandatory_miss_ids": ["g-miss"]}


class MethodFake:
    def __init__(self, *, fail=False, atom_id="p"):
        self.seen, self.fail, self.atom_id = [], fail, atom_id

    def predict(self, *, method, document, config):
        self.seen.append((document, config))
        if self.fail:
            raise ValueError("PRIVATE_EXCEPTION_SENTINEL")
        return {"document_id": document.document_id, "atoms": [{"id": self.atom_id,
            "source_span_ids": ["S"], "mandatory": True, "conditional": False,
            "administrative": False, "uncertain": False}], "assessments": [{"atom_id": self.atom_id,
            "status": "supported", "retrieved_ids": ["vendor-record"], "support_ids": ["E"],
            "counterevidence_ids": []}], "usage": {"input_tokens": None, "output_tokens": None,
            "cost_usd": None, "latency_ms": None}}


def descriptor(method=None, *, marker="synthetic", restart=False):
    """Authored in memory from closed native schemas; fake versions/file hashes never mean
    installed dependencies or execution evidence."""
    profile = SEMANTIC_PROFILE if method else RENDER_PROFILE
    selected = ("parse", "segment", "retrieve", "validate") + (() if method == "B0a" else ("judge",)) if method else ("render", "ui")
    components = {}
    for group in selected:
        libraries = () if method == "F2" and group == "judge" else LIBRARIES[group]
        body = {"status": "available", "files": [
            {"path": path, "kind": "python_ast" if path.endswith(".py") else "text_lf",
             "status": "available", "sha256": fingerprint([marker, path]), "reason": None}
            for path in sorted(GROUPS[group])], "dependencies": {
                name: {"status": "unavailable", "version": None, "reason": "distribution_not_installed"}
                if name == "typesafe-sdk" else {"status": "available", "version": "synthetic-only", "reason": None}
                for name in libraries}}
        components[group] = {**body, "fingerprint": digest("m3.source-component.v1", body)}
    identity = {"method": method, "parser_profile": None, "components": components,
        "runtime": {"python_implementation": "synthetic", "python_version": "synthetic",
            "unicode_version": "synthetic", "platform": "synthetic", "byteorder": "little",
            "canonical_policy": CANONICAL_POLICY}}
    value = {"schema_version": 1, "profile": profile,
        "status": "restart_required" if restart else "available",
        "fingerprint": digest("m3.fingerprint-descriptor.v1", {"profile": profile, "identity": identity}),
        "identity": identity, "restart_required": restart,
        "reasons": ["restart_required:validate"] if restart else [],
        "execution_verified": False, "attestation": False}
    return {**value, "descriptor_sha256": digest("m3.fingerprint-descriptor-record.v1", value)}


@pytest.fixture
def eval_kit():
    """Synthetic comparison/custody/provenance fakes shared by tests/eval modules."""
    return SimpleNamespace(setup_comparison=setup_comparison, CustodianFake=CustodianFake,
                           MethodFake=MethodFake, descriptor=descriptor)
