# Prospective comparison boundary

`rfp_matrix.eval.runner` is injected-only source tooling. It does not run from the
CLI, open files, locate gold, install adapters, or call a provider. No evaluation
has been executed. All F1/F2 mock/replay receipts remain protocol evidence only.

The public API is `freeze_comparison(public_inputs, split=..., pins=...,
method_configs=..., dataset_counts=...) -> FrozenComparison`, followed by
`run_comparison(frozen, public_inputs, ports=..., custodian=..., seal=...) -> dict`.
Every method receives the identical ordered raw PDF/vendor bytes and registered
source/evidence span inventory. The producer must use the literal resolver to
build `PublicSpan`; this runner checks registered membership and original-string
interval shape, not PDF extraction or semantic correctness. It rejects changed
bytes, registries, configurations, document/family counts and duplicate PDFs.

`pins` contains SHA256 commitments for source, parser, normalizer, questions,
thresholds, split, labels, scorer, budget and price. `labels` is an opaque digest,
never label data. Every arm (B0a/B0b/F1/F2) supplies exactly `config_sha`,
`model_snapshot`, `sdk_snapshot`, `price_sha`, `budget_sha`, `origin`. The common
price digest covers the per-provider price schedule; a common budget digest
covers identical worst-case caps. Exact model/SDK strings are prospective
declarations, not verified pins. Unadmitted arms must say so, not use an alias as
an allegedly frozen snapshot. Live origin is deliberately rejected in this
fake-only construction boundary. No budget authorization is inferred from a seal.

Each injected `FakeMethodPort.predict(method=..., document=..., config=...)`
returns only document_id, atoms, assessments, usage. Atoms hold ID, registered
source span IDs and four boolean flags. Assessments hold atom_id, closed status,
retrieved record IDs and support/counterevidence sentence/window IDs. Each selected
span's owning record must be in the bounded retrieval. One assessment per atom and
at most ten retrieved records are required. Usage contains input/output tokens,
cost_usd and latency_ms, each numeric or null. Duplicate predictions are retained
as separate atoms for one-to-one adjudicated scoring; no semantic match is inferred
from overlap. Baseline output must not be substituted for unavailable typed arms.
B0b and F1 must replay the identical frozen typed inventory: when both are present,
an inventory mismatch fails both arms before scoring. Missing arms leave this
paired matching-ablation check unmeasured.

The separate custodian implements two hooks:

- `authorize_and_consume(frozen_manifest=..., seal=bytes) -> Authorization` must
  authenticate the caller and real independent owner, verify rights, sealed split,
  adjudication, private label digest, exact source/scorer/configuration and family
  eligibility; atomically reject spent or development-exposed families; and consume
  the set before returning. Aborts and failed arms leave it spent. Seal bytes are
  supplied externally; this module never creates CUSTODY or claims a human role.
- `score(authorization=..., method=..., predictions=...)` operates against private
  gold in that separately isolated boundary and returns the frozen population
  digest and allowed metric count pairs. Missing metrics remain unmeasured; zero
  denominators are N/A. See [pipeline](PIPELINE.md) for authorized opaque failure ID
  disclosure; other private diagnostics stay out.

Python Protocol typing is not a sandbox, signature verifier or durable custody
implementation. The runtime host must isolate method requests/indexes/caches from
private storage, prevent source IDs or filenames encoding answers, authenticate
seals, pin the actual code digests and implement an atomic spent ledger. Caller
fakes only demonstrate intended seams later; they cannot certify custody. Private
raw predictions, mandatory miss IDs, disagreements, family summaries/bootstrap,
retrieval MRR and detailed failure diagnostics stay at the custodian; public
receipts allow aggregate counts only. Author disclosure requires a recorded spent
set decision, then new unseen families for fresh confirmation. Existing public
`score_packet` remains development-only and retains its richer arithmetic,
one-to-one matching, N/A and family uncertainty behavior.

The runner sanitizes exception text, unknown metric fields and free-text returns.
An invalid arm becomes `failed_unmeasured`; missing/unavailable arms remain
`not_yet_measured`. It never emits model-quality acceptance, automatically lowers
floors, fills missing scores, or treats an all-abstain denominator as perfect.
Registered source text is public untrusted data; adapters must not treat it as
instructions. The outer host, real isolation and actual APIs remain unverified.

The current fake-only v3 manifest optionally includes run_envelopes, produced by
pipeline_envelope_commitments from actual D4 estimator source. See ENVELOPES.md
for exact registry context and native bound/cost/unknown semantics. Frozen
commitments and budget-policy digests must match the external seal; they are
prospective data, separate from actual method usage and metrics. Missing estimates
remain not_yet_estimated; no live authority is created.

See [frozen build provenance](BUILD-PROVENANCE.md) for full normalized semantic contexts, separate render identity, changed-pin refusal and explicit legacy limitations. Descriptors establish integrity only; runtime, security and quality remain unverified.
