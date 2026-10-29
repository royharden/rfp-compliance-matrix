# Prospective full-run bounds and sealed receipts

No planner, comparison, model or evaluation has been executed. The canonical pure
producer is `providers.envelope.estimate_run_envelope(config, registry_counts,
method=..., origin=..., approval=..., budget_context=...)`. It bounds bundled logical requests,
one-retry transport attempts, questions, tokens, concurrency/deadlines and Decimal
declared-price cost. It performs no SDK/key/ledger/DB/network operation. Bounds
are algebraic assumptions, never observations, authenticity or permission.

Native counts require schema_version, artifact_sha, corpus_generation,
registry_sha256, assessment_generation, matrix_generation, inventory_candidates,
requirement_rows, retrieval_top_k and max_windows_per_candidate. Optional actual
retrieval/oversize/replay counts are producer-validated. No private/gold or
human-corrected count belongs in a blind arm.

`eval.envelopes.pipeline_envelope_commitments(ports, registry_counts,
method_configs, budget_contexts=...)` calls that actual producer through each port's disabled
`prospective_policy(document_id)`. Counts map method → public document → native
mapping. Before blind classification, requirement rows conservatively bound all
frozen source candidates. The trusted host supplies real generation/registry
attestations; missing context remains not_yet_estimated or partial_unknown.
No generations, retrieval result or labels are invented to fill missing counts.

Pass this result as `run_envelopes` to `freeze_comparison`. The v3 fake-only policy
seals four-arm/document native envelope/config/count digests, raw/artifact/corpus
identities and budget policy before dispatch. Receipts keep prospective bounds
separate from actual nullable usage/metrics. Each closed commitment exposes logical
requests, retry-inclusive attempts, token bounds, concurrency ceiling/deadline,
declared Decimal cost/unknown, whole-run affordability/unknown and condition codes.
Evaluation duplicates no pricing/fanout algorithm.

Unknown price stays null, never zero. Disabled zero budget stays explicit. Input,
window or replay uncertainty stays partial. Mock/replay incremental zero cannot
be a measured live cost or quality advantage. Partial budget means incomplete
evaluation. Source-only runner origin remains non-live and quality acceptance false.

Changing any commitment changes the freeze and requires matching external
authorization/seal. Unknown fields and raw-input/budget mismatches are rejected.
The real external custodian must authenticate actual source/config/registry/price/
approval pins and durable spent policy; a digest alone establishes no authority.
The provider layer's later-live guard recomputes native algebra and exact prospective approval.
Real SDK framing/output caps, scheduling, prices and budget enforcement still
require later runtime validation.

Explicit budget contexts map method/document to the native schema_version,
document_id, origin, batch_committed_usd, document_committed_usd and
ledger_snapshot_sha256. Missing remaining-balance context is UNKNOWN, never a
fresh budget cap. Gross cap arithmetic is separate from whole-run affordability.
Each frozen item retains budget_snapshot_sha and remaining_budget_basis. The
cold evidence bound uses max(inventory_candidates, requirement_rows) * top_k;
five/two questions are bundled, not separate per-window calls.
