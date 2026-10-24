# Provider construction boundary

The CLI's default B0a path uses no provider. B0b asks typed inventory questions and uses rule evidence matching; F1 asks typed inventory and relation/registered-window questions; F2 uses the same bounded classification/selection port for a non-Jev comparator. All arms retain the same source pointers and human decision controls. Method names describe source paths, not measured results.

`DisabledProvider` is the default for every typed path: no SDK import, credential read, request or charge. A disabled or malformed result produces `needs_review`, never confident corpus absence. Lazy TypeSafe 0.7.5 and stdlib OpenAI-compatible transport source now exists in `live.py`; live execution remains unadmitted. Public research and exact artifact hashes are in [PROVENANCE.md](PROVENANCE.md). No live semantic quality, cost or latency was measured.

`MockProvider(responses, requested_model=..., method="F1", semantic_fingerprint=None)`
and `ReplayProvider(receipts, ...)` accept caller-supplied exact-request responses.
Their current key is `digest('m3.judgment-request.v2', {'state': state,
'questions': questions, 'model': requested_model, 'method': method,
'semantic_fingerprint': semantic_fingerprint,
'question_policy': 'm3-typed-registered-candidates-v1'})`.
Missing semantic binding is explicitly legacy/unavailable, not automatically
rekeyed; original mappings remain inspectable on the caller-owned port. Explicit
judge checks current semantic source/process before consuming current records.
Fakes stay mock with no live model/usage/cost; replay preserves original provenance
and separate zero incremental cost. No historical result becomes live.

The injected `judge(state, questions)` port returns schema-v1 `status`, `origin`, `answers`, optional `model`, and optional `usage`. Closed typed questions require exactly matching answer IDs. Choice answers include type, registered choice, full probability distribution, and confidence; Noul answers contain type and yes probability. Unknown IDs, malformed/nonfinite distributions and missing answers fail visibly. Context remains untrusted data, with no actor, tool, approval or export authority.

Inventory asks independent requirement/mandatory/conditional/administrative/atomic questions. Evidence first checks literal registered spans, then asks relation and sentence/window selection. Confidence thresholds are provisional policy, not calibration evidence. Low confidence/incomplete coverage remains reviewable; counterevidence survives. No typed result grants approval.

`CallLedger` is the durable accounting authority for the bounded dispatcher.
Its default budget is zero, complete pinned prices are mandatory, both possible
attempts are reserved atomically before sending, and unknown usage retains its
reservation. Source/static checks alone have been performed; none of the runtime
accounting or provider behavior has been exercised during Dirty Mode.

## Factory and configuration seam

`ProviderConfig.from_mapping(mapping)` rejects unknown fields, including credentials.
`create_provider(config=None, *, client=None, http_client=None, ledger=None)` returns
a `JudgmentPort`; `judge(state, questions)` is unchanged. Construction does no I/O.
The default has `enabled=False`, `budget_usd="0"` and no admission reference.
Live policy needs exact model/SDK, artifact/license/terms references, complete
decimal input/output prices and their policy/provenance, a token-bound policy,
source/question policies, a positive explicitly approved budget, and an
`admission_reference`. `approved_config_sha256` must equal `config.config_sha256`
after those policy fields are frozen. These are recorded caller admission inputs,
not credentials or model-generated authorization. Keep them outside prompts.

F1/B0b use `sdk_name="typesafe-sdk"`, `sdk_version="0.7.5"`, and the selected
`model="jev-1.13.0"`. F2 uses `sdk_name="python-stdlib-urllib"`, the exact target
Python version as `sdk_version`, and a caller-selected dated GPT snapshot with
`model_provenance`. The inspected historical `gpt-4.1-mini-2025-04-14` is deprecated
and is not an admitted default; confirm a supported comparator before activation.
No optional SDK is imported until a future admitted dispatch. Injected fake clients
may implement `system_one(state=..., questions=..., model=..., timeout=..., retry=...)`
or `post(body, *, timeout)` respectively; SDK fakes receive `{"max_retries": 0}`.
Injected clients are explicitly `origin=mock`, with `model=None` and simulated
usage semantics. They require `CallLedger(..., origin="mock")`; the default live
ledger is rejected. Simulated ledgers cannot be reopened as live accounting under
the same path. Their receipts expose `simulated_cost_usd`, never an actual charge.

F2 returns validated closed `raw_classifications` (Noul boolean/null or registered
Choice ID), raw provider usage/finish metadata and
`distribution_semantics="generated-closed-labels-no-probabilities"`. Its status is
`needs_review`, with empty `answers`; the probability-consuming evidence engine
therefore remains conservative. A comparative runner may explicitly consume the
raw labels as F2 predictions, retaining the distinct semantics. Generated numbers,
one-hot encodings or token logprobs must never be presented as Jev distributions.

## Bounded dispatch and later activation

P4.2 connects the factory to `BoundedDispatcher(transport, *, ledger)` when all
admission inputs are present. A missing ledger returns disabled. Construct
`CallLedger(path, budget_usd=config.budget_usd,
input_per_million_usd=config.input_per_million_usd,
output_per_million_usd=config.output_per_million_usd,
price_policy=config.price_policy)` using **one shared machine-local ledger path**
for all application instances. Construction does not open/create a database.
Judge/dispatch is the later runtime boundary; do not invoke it in Dirty Mode.

Live configuration requires `document_id`, `source_sha256`, `corpus_sha256`, and
`model_provenance`; hashes bind the exact immutable source/corpus generations.
The approved config hash covers these bindings. Configured limits cannot exceed
$1/document and $10/shared-ledger batch. The dispatcher checks that the ledger's
actual price/budget policy matches the configuration. A ledger cannot silently
change its pinned price/budget after use.

The ledger reserves the configured input/output cap for two attempts in a single
transaction before the first send. Each attempt claims a durable slot; at most
two slots can be active across clients/processes sharing this ledger. One retry
total is allowed on a typed retryable transport/timeout/rate-limit outcome, with
50–250 ms jitter inside the same <=60-second dispatch deadline. All SDK internal
retries are disabled. Missing/unknown actual usage never becomes zero. Reported
token/cost overruns halt future dispatch and withhold successful typed answers.

The conservative preflight rejects requests whose complete transport body UTF-8
byte count plus 1024 exceeds the input cap. Its exact policy string is
`utf8-byte-upper-bound-plus-1024-v1`. **Provider tokenizer/framing and TypeSafe
distribution-output bounds remain unverified assumptions**; this policy requires
explicit admission and must be validated against actual usage before live use.
OpenAI output cap is sent in the request. TypeSafe has no inspected output-cap
parameter, so its output cap is an accounting reservation, not an API guarantee.
Keep this path disabled until those bounds and the complete request fanout fit
the authorized budgets. A token overrun halts even if output price is zero.

`dispatcher.dispatch(state, questions, *, cancel=None)` optionally accepts a
`threading.Event`; the two-argument JudgmentPort delegates to it. Cancellation is
checked before reservation and dispatch, during capacity waits, retries and waits
for a result. An already-sent call cannot be guaranteed canceled by sync HTTP.
The caller stops consuming it at deadline/cancellation; the worker retains its
slot until the actual transport ends and settles reported usage. SQLite
transactions use a bounded lock timeout; OS/SDK/DB scheduling and hard wall-clock
return timing remain runtime-unverified. There is no replacement worker or lease
expiry that could exceed the two-call cap. A killed process can strand a slot;
operator reconciliation is required before freeing it, with ambiguous charges
retained. Automatic crash-charge reconciliation is deliberately absent.

Request/cache identity hashes origin, exact state/questions, source/corpus hashes,
model, SDK and provenance, policy/config versions and approved budget bounds.
Identical in-flight/unreconciled requests cannot redispatch. Completed receipts
replay with `origin=replay`, original provenance/cost and separately zero
incremental cost. A receipt created at timeout preserves its original unknown
cost even if later ledger settlement arrives; consult ledger accounting for the
later truth. No receipt is upgraded into a new successful model prediction.

## Portable offline tape and diagnostics

Exports from `rfp_matrix.providers`:

* `replay_identity(state, questions, *, config)` hashes exact state, full questions,
  registered candidate shape, method/model/SDK/provenance, policy versions and
  immutable artifact/corpus hashes. No raw input state is stored. Here
  `source_sha256` means `artifact.artifact_sha`; `corpus_sha256` means its generation
  digest. Budget/enable/admission flags are excluded so disabled offline replay
  can match a historic request without granting live execution.
* `make_replay_record(identity, result, *, record_kind)` sanitizes a
  `captured_live` or `authored_mock` result. Extra transport payloads/headers are
  omitted; imported tape schemas reject extra keys. Closed answer distributions,
  F2 labels, nullable usage and original cost survive. Mock records cannot assert
  observed live model/SDK/charge; a replay cannot be recaptured as a live record.
* `write_replay_tape(path, records)` creates a new local `*.m3-replay.json` file
  exclusively. Existing parent required; no overwrite, directory creation or
  credential lookup. Limit 2 MiB and 256 unique request identities. It returns
  hashes/counts; it does not authenticate who captured the contents.
* `load_replay_tape(path, *, config)` explicitly reads a bounded regular local
  tape, rejects link/junction/traversal/stream paths, duplicate JSON keys and
  altered hashes/shapes, and returns a `FileReplayProvider` with the normal
  `judge(state, questions)` port. Missing/mismatched requests are unavailable;
  there is no live fallback. Config may remain disabled/zero-budget, but exact
  artifact/corpus/model/SDK/question/source/provenance identities must match.
* `provider_diagnostics(config=None)` is pure: configuration/gate summary only,
  no key-presence check, SDK import/version lookup, filesystem, DB or dispatch.
  A configured hash match does not claim external live authorization or runtime
  verification. Invalid configuration returns a bounded stable status.

Replay results always have `origin=replay`, `original_origin`, `record_kind`,
`original_observed_cost_usd` (possibly unknown), separately zero
`incremental_cost_usd`, `integrity=hash_checked`, and `authenticity=not_verified`.
Original mock simulation costs remain distinct. Hash integrity is not trusted
authenticity: someone able to author/reseal a file can claim a captured-live origin;
that claim cannot satisfy independent live evaluation or custody evidence.
No raw provider response, header, arbitrary field or source input is exported.
Files are immutable by API convention/exclusive creation and hash checking, not
an OS access-control boundary. Concurrent hostile filesystem replacement,
cross-platform junction behavior and actual durable write/replay remain untested.

## Prospective whole-run envelope

`estimate_run_envelope(config, registry_counts, *, method=None, origin="live",
approval=None, budget_context=None)` is pure. It never traverses a registry,
imports an SDK, reads keys/files/DBs or dispatches. Trusted application code
supplies the exact deterministic registry counts and immutable identities.

Required `registry_counts`: `schema_version=1`, `artifact_sha` (same meaning as
config source_sha256), `corpus_generation`, `registry_sha256`,
`assessment_generation`, `matrix_generation`, `inventory_candidates` and
`requirement_rows` (0..2000), `retrieval_top_k` (0..10),
`max_windows_per_candidate` (0..20000 before Choice truncation). Optional fields:
`retrieved_evidence_candidates` (current deterministic retrieval total or null),
`inventory_oversize_requests`, `evidence_oversize_requests`, and
`replay_covered_requests` (null means unknown). Unknown/extra fields fail closed.

B0a has no model requests. B0b bundles five inventory judgments into ONE request
per candidate. F1/F2 add ONE two-question relation/selection request per potential
requirement/evidence-record pair. A fresh typed inventory can retain candidates
previously rejected: the full-run evidence upper bound therefore uses
`max(inventory_candidates, requirement_rows) * top_k`. Current retrieval totals do
not tighten that prospective bound. Up to254 registered windows plus `none` are
Choice options, not extra requests. One controller retry doubles attempts/tokens;
hidden retries remain disabled. Input/output caps cover the complete bundle.
Concurrency is a two-call ceiling, not a guaranteed parallel schedule; each
logical request has one <=60-second deadline including its retry. The sum of
logical deadlines excludes CPU/DB/OS/scheduler overhead and is not measured time.

Price/cost uses bounded decimal text (<=1e9, at most18 fractional places) with
80-digit local precision. Missing either price, policy or provenance is unknown,
never zero; a zero-call baseline is mathematically free. Mock/replay incremental
cost is separately zero, while projected live cost/original replay cost may be
unknown. No cost is measured. Replay coverage must be supplied or remain unknown.
Truncated options and oversize requests are explicit incomplete-plan conditions.

Gross declared budget affordability is separate from remaining affordability.
No budget snapshot means remaining balance is unknown. An optional exact
`budget_context` has schema_version,document_id,origin,batch_committed_usd,
document_committed_usd,ledger_snapshot_sha256. `CallLedger.budget_snapshot(id)` is
an explicit **later runtime accounting operation** (opens/initializes its schema),
never called by the pure estimator or factory. Unknown charges retain reservation; legacy unscoped calls count against
every document conservatively. Current budgets remain zero. The snapshot is not
a full-run reservation: concurrent ledger changes can still prevent completion.
Partial budget exhaustion remains incomplete evaluation, never successful absence.

The output binds config/source/corpus/method/model/SDK/policies, registry counts,
budget snapshot and all algebra in `envelope_sha256`. Optional prospective
approval has EXACT schema_version,envelope_sha256,config_sha256,source_sha256,
corpus_sha256,method,origin,registry_counts_sha256,approval_reference,
assumptions_acknowledged. It compares a supplied record; it authenticates no
approver and grants no authorization. Tokenizer/framing, TypeSafe output-cap and
runtime deadline assumptions remain explicit and unverified.

`create_provider(..., run_envelope=None, run_approval=None)` requires a matching
recomputed full-run envelope and prospective record for a future **real live**
transport, in addition to all existing config/ledger/caller authorization gates.
Missing/mismatched/unknown/insufficient plans return disabled. Injected clients
remain explicitly mock with mock accounting; replay stays keyless. An unused
wrong-method fake argument cannot bypass the live gate. No HTTP endpoint enables
live or accepts approval/provider configuration. This source has not been run.

## Source identity and process pins

`rfp_matrix.provenance` exports `source_fingerprints()`,
`semantic_policy_fingerprint(method, parser_profile=None)`,
`render_policy_fingerprint()` and pure
`verify_fingerprint_descriptor(value, *, profile=None)`.
Collection is explicit; config/disabled/provider factories never collect.

The closed descriptor has schema_version,profile,status,fingerprint,identity,
restart_required,reasons,execution_verified=false,attestation=false and
descriptor_sha256. Profiles are `m3-source-components-v1`,
`m3-semantic-policy-v1`, `m3-render-policy-v1`; status is available/unavailable/
restart_required. Identity has method,parser_profile,components,runtime. Component
records use fixed package-relative paths, canonical AST hashes for Python and
UTF-8/BOM/CRLF-to-LF text hashes for resources. Python comments/docstrings/positions
are excluded by the versioned canonical policy. Runtime identity contains Python
implementation/version, Unicode version, platform, byteorder and canonical policy;
relevant distribution versions are collected through metadata without SDK imports.
Unavailable versions are null with explicit reason, never guessed. Baseline omits
the judge SDK component; F2 stdlib scope excludes unrelated TypeSafe metadata.

Semantic parse/segment/retrieve/judge/validate groups exclude UI/docs/tests and
export rendering. Render has separate export/UI groups, including fixed packaged
templates/CSS. CSS/docs/tests cannot change semantic approval identity. Fixed
source files that cannot be safely read have null hashes and unavailable status.

`fingerprint` binds only deterministic profile/identity. PID, process creation,
file mtimes, absolute paths and restart status/reasons never enter that identity.
`descriptor_sha256` separately binds the full record/status for integrity. Neither
is a signature, trusted attestation or execution/compatibility/security proof.

At explicit semantic/render collection, process-local component pins are sticky:
changed/missing selected source after a prior pin requires restart. Before a first
pin, source newer than the real process start is conservatively restart-required;
unknown process-start metadata also fails closed. Current source collection is not
proof that its disk bytes were loaded or assessed. Windows own-process timing uses
GetProcessTimes; Linux has a proc/boot-time adapter; other platforms currently have
no creation-time adapter. No watchers, hot reload, persistent alternate truth or
reset-to-current API exists. A clean restart with identical source/environment has
the same deterministic semantic fingerprint. All behavior remains unrun.

Config adds optional `semantic_fingerprint=""`. A future real-live factory also
needs `semantic_provenance=...` (pure closed descriptor validation), matching
method/config hash and the run-envelope/prospective record gates. TypeSafe SDK
metadata must match its exact pin; F2 stdlib version must match Python metadata.
Actual judge/attempt recollects current source/process before SDK/key/DB dispatch.
Missing/mismatch/restart returns unavailable. Defaults remain zero/disabled.

Portable replay identities now use schema2/current semantic binding. Explicit
`replay_identity(..., semantic_provenance=None)` collects current semantics, or
purely validates a supplied descriptor; file replay judge always checks actual
current source/process. Legacy identity-v1 records still load and are inspectable
via `policy_history()`, but cannot match/relabel a new current v2 request. Captured
live v2 results must carry the same semantic fingerprint and cannot be rebound
to newly edited source. History and authored mocks never become live evidence.

## Later-session read-only policy identity

`review_policy_identity(config, *, semantic_provenance)` returns a frozen
`ReviewPolicyIdentity`, exported from `rfp_matrix.providers`. The factory is
pure: explicitly supplied config/descriptor only, no source collection, SDK,
keys, ledger, tape, transport or approval consumption. Config must have
`enabled=false`, `budget_usd="0"`, empty `admission_reference` and
`approved_config_sha256`; this path cannot grant live authority. Nonsecret
model/SDK/method, exact document ID/artifact SHA/corpus generation, fixed question
and source policies, and exact semantic fingerprint remain required.

Handle fields: `config` (validated immutable ProviderConfig, or None if invalid),
`status` (`available`, `unavailable`, `restart_required`), fixed-code `reason`,
`identity` (read-only mapping), and `semantic_provenance` (defensive descriptor
copy). `identity` contains only method/model/sdk_name/sdk_version/question_policy/
source_policy/document_id/source_sha256/corpus_sha256/semantic_fingerprint.
It has no price/budget/admission/free-text fields. `read_only_policy=true`,
`execution_verified=false`, `live_authorization_verified=false`.
The handle has **no judge or dispatch method** and is not a JudgmentPort.
Exported `is_review_policy_identity(value)` uses `type(value) is
ReviewPolicyIdentity`, without evaluating arbitrary object properties. Its closed
`purpose` is exported `REVIEW_POLICY_PURPOSE="review-policy-identity-only-v1"`.
Evaluation/inference consumers must reject the actual type before consuming an
execution seal; a marker string or config-shaped duck type alone is not trusted.

The supplied descriptor must pass the closed pure integrity validator, match
method/semantic fingerprint, be available and not require restart. F1/B0b need
actual supplied TypeSafe metadata matching SDK 0.7.5; F2 needs matching Python
stdlib version and retains its uncalibrated generated-label semantics. Missing
or mismatched metadata fails closed. A valid descriptor is not independently
trusted currentness, author authenticity or proof of historical execution.

The explicit CLI/application operation must collect actual current provenance,
bind the handle independently to each run/artifact/corpus, and compare recorded
policy/model/SDK/question/source identities through the evidence owner. Do not
compare the handle class or disabled config execution flags as if they identify
the historical prediction transport. Never derive current proof from captured
assessment data, reuse one document's config for another run, rehash old evidence
as reassessed, or pass this handle to inference/evaluation adapters. Review still
requires semantic currentness, original evidence integrity, existing decision
CAS and genuine human action. F2 remains review-required; this handle does not
calibrate labels or turn mock/replay into live observations. All behavior UNRUN.

`policy_identity_projection(config) -> dict[str, Any]` is the shared **pure**
ten-field projection used by `handle.identity` and the genuine prediction receipt
producer. It accepts an actual execution `ProviderConfig` or closed mapping
without changing enabled/budget/admission fields or evaluating live admission.
It validates fixed policies and exact source/document/corpus/semantic binding,
raising sanitized `M3Error(code="review_policy_identity_invalid")` when missing
or invalid. An unavailable handle with incomplete identity exposes an empty
mapping. The helper performs no descriptor collection, SDK/key/DB/transport I/O,
approval consumption or execution/currentness assertion.

Only a genuine new assessment may record the projected prediction identity;
an old absent receipt remains legacy. Comparing the new receipt with a read-only
handle does not require the same full admission config hash or runtime provider
class. This semantic comparison never backfills or rebinds historical proof.
