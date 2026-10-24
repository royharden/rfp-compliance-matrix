# RFP compliance matrix

A local Python CLI/TUI for reviewing a native-text PDF RFP against an explicitly
fictional vendor corpus. It preserves literal source spans, supporting and
contradictory evidence, explicit human decisions and a complete HTML/CSV/Markdown
matrix with generation lineage. It helps reviewers inspect evidence; it does not
establish legal compliance or commit a vendor.

## Status

**Construction source — runtime unverified.**

- **Built (source only):** the Python core, CLI/TUI review workflow, SQLite review
  store and LangGraph pause/resume source, deterministic HTML/CSV/Markdown exports,
  optional local browser reviewer, and evaluation/scoring source, listed below.
- **Not executed:** no test has been run or collected, and no app, parser,
  database, LangGraph graph, template render, server, model or evaluation has been
  executed. No live model or paid API call has been made; dependency versions are
  unresolved and there is no lockfile.
- **Construction caveat:** the code was written in a static-only construction
  phase ("Dirty Mode"). Checks were limited to syntax parsing and source
  inspection, so working behavior is an assumption until validated.
- No measured quality, speed, cost, security or release-readiness claim is made.
- **Next step:** [activation and validation](docs/ACTIVATION.md) lists the bounded
  setup and runtime checks still required.

## Source capabilities

- Immutable source/artifact identity and original Unicode anchors; explicit
  per-page coverage and separate versioned human inspection policy.
- Structural/modal inventory, BM25, registered evidence and conservative gap/
  counterevidence states; injected mock/replay ports and lazy gated provider source.
- SQLite decision authority, actor-owned coverage events, exact-revision approvals,
  pending edits, versioned split/merge/applicability and stale source/corpus changes.
- CLI/TUI review and source navigation, LangGraph pause/reconciliation source,
  deterministic all-row exports and recovery/maintenance source.
- Development scorer with one-to-one adjudicated atoms, explicit denominators,
  mandatory misses, family uncertainty and four unmeasured method rows.
- Prospective fake-port comparison freeze with an external consumed-seal/scorer
  boundary; rights/family/annotation contracts that create no permission or custody.
- Actual four-arm source/evidence pipeline adapter source, closed provenance and
  source-only evaluation/custody regression definitions; none have been executed.

## Intended later use

After environment activation and validation are authorized, resolve compatible
licensed dependencies and the lockfile. The intended setup is `uv sync --extra
graph`; it has not been run. The full regression suite installs with
`uv sync --extra test` (dev, web and graph); with a narrower install, browser
route/template tests skip without the web extra. Keep local state outside OneDrive and providers
disabled/zero-budget. The proposed keyless journey is:

```text
rfp-matrix diagnostics
rfp-matrix import <permitted-text.pdf> --vendor <fictional.jsonl> --run demo --terms '<permitted terms>'
rfp-matrix review --run demo --interactive
rfp-matrix resume --run demo
rfp-matrix export --run demo --out <new-output> --matrix-generation <displayed> --request-id demo-export-1
rfp-matrix update-evidence <vendor-v2.jsonl> --run demo --matrix-generation <displayed>
rfp-matrix review --run demo --queue stale
rfp-matrix verify-export <bundle>
```

Commands are unexecuted intended interfaces. Vendor JSONL uses record_id, text,
terms, fictional:true and optional source_url. No real vendor secrets or credentials
belong in inputs. Use the exact displayed generation: edits and stale sources do
not silently retain current approval. Similarity and model confidence are never
approval; positive decisions and gap acknowledgments remain distinct. All unresolved
rows stay visible in exports. Coverage/maintenance/provider CLI flags must follow
the accepted CLI implementation. Current source exposes `coverage --run demo`,
`coverage --run demo --interactive` (TUI V), `diagnostics --run demo`,
`backup --out <new-bundle>` and `restore <bundle> --destination <new-local-state>`.
Coverage inspection/reassessment uses exact displayed hashes/revisions, documented
in the workflow; no maintenance command above has been run.

[Workflow](docs/REVIEW-WORKFLOW.md), [architecture](docs/ARCHITECTURE.md),
[activation](docs/ACTIVATION.md) and the proposed [two-minute demo](docs/DEMO.md)
explain the source paths and future checks. [Development scoring](eval/README.md),
[comparison boundary](eval/RUNNER.md), [corpus workflow](eval/CORPUS-WORKFLOW.md) and
[results](results/results.md) retain unmeasured status. The [synthetic example](demo/README.md)
is authored development material and cannot discharge public-corpus/human-gold floors.
The [four-arm adapters](eval/PIPELINE.md) use product producers with injected ports;
the runner deliberately rejects live origin. Failure/miss IDs require explicit
external disclosure authorization after prospective spent-set consumption.

## Limits and pending validation

Dependency pins and real parser/SQLite/checkpointer/provider compatibility remain
unverified. Scans/OCR, autonomous commitments, proposal submission,
cloud deployment and collaborative SaaS are outside this scope. Images, extraction
errors and uncertain reading order cannot be cleared by a character-count shortcut.
Human inspection is a source workflow, not proof of extraction completeness.

Lazy provider configuration/factories are source-only, explicitly gated and zero
budget by default. A credential name or config hash grants no live authority.
Confirmatory evaluation still needs lawful public families, staffed annotation and
independent adjudication/custody, actual source/config/model/SDK/license/price seals,
durable spent-set isolation, an admitted live comparison host and independent
nonauthoring product acceptance. No private gold or CUSTODY file is supplied here.

Later activation must exercise literal anchors, source/evidence invalidation,
restart and concurrency failures, backup consistency, unsupported checkpoints,
idempotency, generation races and deterministic exported artifacts. See the
activation document for the bounded deferred list. No paid calls, real external
accounts, deployments or publication were performed.

## Optional browser reviewer

The selected local browser reviewer now has source templates, CSS and backend/application/presentation seams. It has never been rendered or run. See [browser workflow](docs/BROWSER-REVIEW.md) for actual run-scoped routes/forms, optional web dependencies, trusted challenge delivery and future claimed-loopback activation. CLI/TUI remains the minimum workflow. No server or browser was started; no security/accessibility verification is claimed.

CLI defaults to versioned JSON; use the global --output text for readable noninteractive output. Keyless --replay-tape requires matching disabled --provider-config and forbids --allow-live. No call or live budget is authorized.

The source-only [amendment comparison](docs/AMENDMENTS.md) shows literal old/current
requirements, lexical proposals, unmapped mandatory rows and separate human
correspondence history. Exact-version lineage events never carry approval into
a changed source/corpus generation. No amendment comparison or browser render
has been executed. Private login delivery uses --private-login-delivery and must
fail closed unless owner-only bootstrap-file privacy can be established; no
secret terminal/log/URL output is permitted.

## Inventory audit and prospective bounds

The source-only [inventory audit](docs/INVENTORY-AUDIT.md) exposes retained,
rejected, uncertain and unattributed machine candidates, structural gaps and
separate human corrections. Promoting a registered candidate creates a pending
row with no assessment/approval and never changes blind-method predictions.
[Prospective envelopes](eval/ENVELOPES.md) integrate the canonical pure provider
planner into v3 frozen receipts. Counts, tokens and Decimal price bounds are
assumptions; unknown/disabled/partial states remain explicit. No planner, UI,
comparison or evaluation was executed and no live budget is authorized.

See [frozen build provenance](eval/BUILD-PROVENANCE.md) for full normalized semantic contexts, separate render identity, changed-pin refusal and explicit legacy limitations. Descriptors establish integrity only; runtime, security and quality remain unverified.

## Later-session typed review

The source-only [typed review journey](docs/TYPED-REVIEW.md) separates explicit
read-only current-policy identity from inference admission and actual evidence.
Matching configuration cannot refresh an assessment, change mock/replay origin,
make F2 calibrated or approve a row. Missing/mismatched/restart policy stays
noncurrent; trusted application policy and exact decision CAS remain required.
The browser accepts no provider configuration or live-enable field. All new
commands/guards and their regression definitions remain unrun.
