# Provider construction boundary

The CLI's default B0a path uses no provider. B0b asks typed inventory questions and uses rule evidence matching; F1 asks typed inventory and relation/registered-window questions; F2 uses the same bounded classification/selection port for a non-Jev comparator. All arms retain the same source pointers and human decision controls. Method names describe source paths, not measured results.

`DisabledProvider` is the default for every typed path: no SDK import, credential read, request or charge. A disabled or malformed result produces `needs_review`, never confident corpus absence. Lazy TypeSafe 0.7.5 and stdlib OpenAI-compatible transport source now exists in `live.py`; live execution remains unadmitted. Public research and exact artifact hashes are in [PROVENANCE.md](PROVENANCE.md). No live semantic quality, cost or latency was measured.

`MockProvider(responses, requested_model=...)` and `ReplayProvider(receipts, requested_model=...)` accept caller-supplied exact-request responses. They do not manufacture model distributions. Their key is `digest('m3.judgment-request.v1', {'state': state, 'questions': questions, 'model': requested_model, 'question_policy': 'm3-typed-registered-candidates-v1'})`. Model selection is explicit; F2 mocks must declare a non-Jev requested-model identity. Fake results have `origin=mock` and no live model/usage/cost. Replays preserve original provenance/usage and separately show zero incremental replay cost.

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
