# Four-arm producer adapters

`rfp_matrix.eval.pipeline` wires actual source/evidence functions to the fake-only
prospective runner. It has not been imported or executed during construction.
`prepare_public_input` bridges bytes to the bounded `parse_pdf` through a temporary
public-input file on future invocation; an extractor can be injected. Original
PDF hash must match the extraction. Vendor JSONL uses `make_corpus`. Every arm
receives the identical extraction, fictional corpus and literal candidate registry.
D5's actual public seams are `registered_source_candidates(artifact)` returning
the candidates envelope and `registered_evidence_windows(corpus)` returning the
windows envelope. The adapter selects candidate anchors/window pointers and checks
artifact/corpus binding; it uses no private segmentation fallback.

Construct `PipelineMethodPort(method, prepared_tuple, provider_config=...,
factory=..., client=..., http_client=..., ledger=..., inventory_cache=...)`.
Construction has no effects. The lazy default factory is D4 `create_provider`,
using `ProviderConfig.from_mapping`. Policies must remain disabled/zero-budget.
Inject only explicitly labelled fake ports in this boundary. Python injection is
not a sandbox or live authority. F2 needs an explicit dated model/stdlib SDK
identity: there is no admitted default or historical deprecated-model selection.

Use `port.frozen_configuration(price_sha=..., budget_sha=..., origin=...)` for
each arm's config in `freeze_comparison`. The config binds the provider template
and all preparations; actual document/extraction-artifact/corpus identity is bound
per document in provider policy. The field source_sha256 binds artifact_sha in
the current D4/D5 consumer contract; the original PDF hash remains separately
bound in PublicInput and prediction trace. Custody authority must still verify source, parser, normalizer,
questions, thresholds, labels, split, budget and price pins.

B0a uses structural/modal inventory plus rule evidence matching. F1 uses typed
inventory plus typed matching. B0b shares/replays the exact F1 inventory, then
uses rule matching; a shared caller-owned ephemeral inventory cache preserves the
logical extraction receipts. Mismatched inventory fails the pair before scoring.
F2 uses D5's explicit uncalibrated generated-label branch with the same source and
bounded candidates, never fake Noul probabilities. Missing providers remain
unavailable/needs_review, never confident absence. No human edits enter predictions.

The recording-port allowlist accepts only the actual public source/requirement/
context/registered-window and identity fields. Unknown metadata, including gold,
is denied before delegate invocation. It opens no label store/cache/index. Public
document text remains untrusted data, not instructions. Host process/filesystem
isolation and injection resistance still need runtime validation.

Closed assessment `retrieved_ids` are real ranked BM25 **record IDs**, maximum ten.
Support/counterevidence IDs are registered sentence/window IDs whose owning records
were retrieved. Selecting a sentence never promotes its retrieval rank. Each
registered evidence span carries record_id. Product resolvers check literal spans.

Closed prediction traces bind source/artifact/corpus/parser/normalizer/policy/
configuration/inventory digests, exact declared model/SDK, origin, request/outcome
digests, supplied usage/attempt/cache metadata or null, and bounded stage failure
codes with opaque hashed IDs. They go to the external scorer; public receipts bind
their digest. Mock usage stays mock; unknown totals stay null. Replayed extraction
usage is logical per-arm usage, not measured incremental savings. Latency remains
unmeasured. No arbitrary exception text is emitted.

Scorer output requires population_sha = canonical fingerprint of
`{input_sha, labels_sha, split_sha}` from Authorization, binding identical gold
populations across arms. Actual metric denominators stay visible (prediction counts
can differ). Allowed metric count pairs are optional; zero denominators are N/A.
Opaque failure_ids/mandatory_miss_ids are released only when the external
Authorization explicitly enables disclose_failure_ids. Otherwise they are
suppressed. IDs must not encode private filenames/answers. Other private details
stay at the custodian. The set was consumed before dispatch; disclosure cannot
restore eligibility.

`eval.custody.ExternalCustodianAdapter` wires external authority, atomic spent
storage and private scorer ports. `consume_seal_state` only transitions sealed to
spent with prospective identity/role/hash/CAS binding. Atomic persistence and
cross-seal raw/family overlap denial belong to the external store. No actual
private store, CUSTODY file, real human role, signature verifier, scorer service,
live comparison host or runtime acceptance has been created.

Run-envelope integration uses each port's prospective_policy with exact method,
artifact and corpus bindings (B0b has its own provider method but shares F1 typed
inventory). Shared extraction cache identity excludes only the method selector;
source/model/SDK/question/policy identities remain. Native planner/custodian
contracts and prospective-only limits are documented in ENVELOPES.md.
