# Local review application port

Construction source; runtime and security behavior are unverified. `application.py`
adapts the existing SQLite authority, not a second database or independent approval
store. `ReviewerApplication(store, registry=None, export_root=None)` receives only
trusted configuration. HTTP requests cannot select any of those objects/paths.

The port represents one machine-local reviewer. `reviewer_subject` is derived from
the store identity and its internally resolved actor. The optional HTTP adapter
must authenticate a local session bound to that subject and authorize each run;
the application port itself is not a network authentication service.

| Method | Result |
| --- | --- |
| `list_runs(limit=200)` | Bounded run summaries, total and truncation flag |
| `get_run(run_id)` | Snapshot plus full decision events and coverage in one read transaction |
| `list_rows(run_id, queue='all')` | Sorted rows, exact versions, coverage and last source/corpus changes |
| `get_row(run_id, row_id)` | `{snapshot, row}`; rejects a row from another run |
| `source_context(run_id, row_id)` | Top-level resolved `anchors`, adjacent-page context, supporting/counterevidence pointers and versions |
| `coverage(run_id)` | Full ordered pure events, outer ledger metadata and effective summary |
| `decide(run_id, row_id, payload)` | Committed event, refreshed snapshot and explicit graph reconcile state |
| `inspect_coverage(run_id, payload)` | Existing exact-artifact/page CAS service result |
| `export(run_id, payload)` | Durable snapshot/export receipt under the configured root |
| `export_history(run_id, limit=50)` | Bounded committed export metadata/currentness |
| `download_export(run_id, request_id, filename)` | Verified bytes, SHA256, fixed filename, `current`/historical label |
| `export_file(run_id, export_id, filename, allow_historical=False)` | Lower-level allowlisted download; historical access requires explicit opt-in |

All mutations accept closed mappings. Actor, output path, provider policy, module
and credentials are forbidden fields. Required decision fields are `action`,
`expected_assessment_generation`, `expected_row_revision`, `request_id`; optional
fields are `reason`, `answer_text`, `human_validated` (a JSON boolean). An edit
never approves prose. A gap acknowledgement never becomes a positive commitment.

`row_revision` remains the content/approval-binding digest. `decision_revision` is
the distinct opaque decision CAS. The existing `expected_row_revision` request
field carries `decision_revision`; only rows with no such field use the legacy
content-revision fallback. Consumers forward the token and never derive it. Every
decision changes CAS even when text stays unchanged; exact request retries retain
the original event. Graph presentations/events bind both identities. Present but
invalid domain CAS fails closed. CLI `--row-revision` takes the decision CAS;
amendment correspondence row references always use the content `row_revision`.

Coverage fields are `page_index` (zero-based), `action`, `expected_artifact_sha`,
`expected_page_hash`, `expected_matrix_generation`, `expected_coverage_revision`
(null for first event), `request_id`, `reason`, optional `inspected_original`.
The nested D5 pure event has no application timestamp/envelope fields. Acceptance
requires an explicit inspection of the original; image/error/unsupported pages
cannot become supported extraction by acknowledgement.

Export fields are `expected_matrix_generation`, `request_id`, optional `formats`
(a list of `html`, `csv`, `md`). Downloads resolve that run's committed request
receipt, permit only fixed filenames, verify stored hashes, and return at most
10 MiB per web artifact. They never return a request-selected filesystem path.
Historical responses retain the immutable document warning and are labelled by
the HTTP adapter. `current` means checked while reading; later edits can invalidate
it. Consumers combining separate reads must compare matrix/row/coverage versions.

`ReviewError` carries a stable `code` and bounded `details`, including refresh
versions for CAS conflicts. Domain decisions commit before graph projection.
Unavailable/corrupt/unsupported checkpoints remain visible beside committed
events. They never create approval or roll domain truth back.

## Optional foreground browser activation

`serve --port PORT --port-claim-reference REFERENCE --run RUN --private-login-delivery`
is source for later authorized activation. Repeat `--run` for each explicit local
session grant. It has no default port, binds only `127.0.0.1`, forbids protected
ports, and enables no reload worker, proxy headers, browser launch or access log.
The operator must first run the project's `agent-ports` claim/check workflow.
The reference is an operator attestation; the product does not claim to verify
an external machine-specific ledger. No port is claimed by importing this code.

Optional dependencies are lazy. Missing extras fail visibly and never trigger an
installation. Local login uses D4's one-time capability/CSRF/session authority,
scoped to the supplied run IDs and derived reviewer subject. Explicit private-file
delivery creates a new local owner-readable `login.json`. Only its nonsecret path,
login URL and expiry are announced. The user reads it privately; the program never
prints its contents or puts a credential in a URL, terminal, JSON command output
or log. `--bootstrap-console` is removed.

Before any challenge is issued, Windows source creates a protected current-user
owner/DACL at directory and file creation, then verifies canonical owner/DACL from
native handles. It does not use chmod as Windows privacy evidence. Linux source
uses exclusive no-follow creation and checks owner/mode/hardlink/ACL metadata.
Other platforms and failed/unknown privacy policies refuse startup. Privacy is
rechecked immediately before writing a challenge; failure has no terminal fallback.
The private bootstrap file is removed by identity-checked cleanup when the
foreground server exits; unrelated/replaced files are preserved. No actual host
ACL, capability, server or session was created during source construction.

Win32 source assumptions were checked against Microsoft's
[file security guidance](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights),
[security descriptor strings](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-descriptor-string-format),
[CreateDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw)
and [GetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo).
This documents API construction choices; Windows ACL/runtime compatibility and
cross-process privacy have not been exercised.

## Amendment correspondence

`ReviewerApplication.amendments(run_id, previous_matrix_generation=None)` returns
`{schema_version, run_id, matrix_generation, comparison, human_events,
correspondence_revision, approval_carry_allowed:false}`. The pure D5 comparison
report is unmodified; human history is a separate envelope. Default comparison
uses the latest owned historical snapshot from a preceding assessment generation.
An explicit previous matrix must exist in this same run's durable history. Lookup
is bounded at 10,000 snapshots and fails visibly rather than inventing ancestry.

`record_correspondence(run_id, payload)` accepts exactly: `relation`, `old_rows`,
`new_rows`, `expected_old_matrix_generation`, `expected_matrix_generation`,
`expected_old_assessment_generation`, `expected_new_assessment_generation`,
`expected_old_artifact_sha`, `expected_new_artifact_sha`,
`expected_correspondence_revision`, `request_id`, `reason`. Each side is at most
50 unique `{row_id,row_revision}` objects registered in its exact source snapshot.
Relations are `one_to_one`, `split`, `merge`, `added`, `removed`; cardinalities
are checked by the producer's pure validator. Retired current rows are not active
targets. Actor, source spans, provider, approval and comparison ID are not request
fields. The server reconstructs the exact report and comparison identity.

Correspondence has its own comparison-ledger CAS: null/empty only before the first
event, then the last committed revision. Exact request retries replay before fresh
CAS; a changed payload conflicts. Events retain sequence, actor, rationale/time,
old/new identities and row arrays, predecessor and revision. The source transaction
commits the full append-only event atomically. No matrix row, approval, source,
assessment or graph intent is changed. A later source/approval generation makes
old correspondence visibly historical, never a reapproval shortcut.

CLI: `amendments --run R [--previous-matrix-generation SHA]`,
`correspondence-history --run R [--limit 100]`, and
`confirm-correspondence --run R --relation REL --old-row ID=CONTENT_REV ...
--new-row ID=CONTENT_REV ... --old-matrix-generation OLD --matrix-generation NEW
--old-assessment-generation OLD_A --new-assessment-generation NEW_A
--old-artifact-sha OLD_S --new-artifact-sha NEW_S [--correspondence-revision REV]
--request-id ID --reason TEXT`. Global `--output text` gives bounded readable
comparison/history; JSON retains exact pins and independently labelled lexical
proposals. Human correspondence is not semantic equivalence or legal compliance.

## Inventory audit and human correction

`inventory_audit(run_id)` returns `{snapshot,audit,promotions,historical_promotions,
run_id,matrix_generation,assessment_generation,status}`. The audit is derived from
the original frozen machine inventory and code-owned structural registry. Missing
machine reports remain unavailable; no classifier is rerun to fill history. Counts
describe machine attribution, not semantic recall or proof that every requirement
was found. Current/historical human promotions are a separate lineage stream.

`promote_inventory_candidate(run_id,payload)` accepts only `candidate_id`,
`expected_artifact_sha`, `expected_assessment_generation`,
`expected_matrix_generation`, `request_id`, `reason`; optional `interpretation`
(at most 20,000 characters), independent `mandatory`, `conditional`,
`administrative` JSON booleans/null, and `applicability`. No actor, raw source span,
provider, output path or approval is accepted. The application transacts exact CAS,
original-report/policy integrity, registered literal identity, duplicate detection
(including retired atoms), row limits and idempotency. The producer performs no
provider call and returns only an unassessed PENDING row. Actor/time/event metadata
is applied internally and bound in the row's content revision. No decision/resume
intent is created. Unchanged rows retain their content, decision CAS, approval and
assessment generation; only the complete matrix inventory generation advances.

CLI `inventory` preserves the raw report interface. `inventory-audit --run R`
provides the derived audit; `--output text` gives bounded registered excerpts.
`promote-inventory --run R --candidate-id ID --artifact-sha SHA
--assessment-generation GEN --matrix-generation GEN --request-id ID --reason TEXT`
supports `--interpretation`, independent `--mandatory true|false`,
`--conditional true|false`, `--administrative true|false`, and `--applicability`.
Omitting a flag uses its registered source default. Promotion never grants factual
positive approval or changes blind-method predictions.

Export renderer `m3.complete-inventory.audit.v3` captures deterministic
`inventory-audit.json`, original machine-report digests, human promotion events and
row origin metadata alongside every unresolved row. The manifest snapshot retains
the original report. Legacy/v2 persisted intents retain their old renderer bytes.
Missing historical audit is labelled unavailable; it is not reconstructed as a
successful machine inventory. Diagnostics and backups preserve/check promotion
event lineage and the unchanged prior matrix rows.

## Prospective run envelope

`run_envelope(run_id,policy_context)` takes a closed mapping with optional `config`,
`method`, `origin`, `approval`, `budget_context`. An empty mapping is a no-provider
B0a preview. Typed methods require explicit exact nonsecret provider configuration
bound to this run/artifact/corpus; no model, SDK, price, approval or remaining budget
is inferred. Caller-supplied budget/approval records remain declared, not trusted
authentication or an authorization to spend.

The application derives candidate/window/current-row/retrieval counts from actual
deterministic registered source and corpus bytes. Pure request-body construction
supplies input-cap diagnostics; no SDK/client, key, live ledger or transport is
invoked. D4's unmodified estimator returns algebraic worst-case bundled requests,
retry/token/concurrency/deadline/declared-cost bounds with explicit assumptions.
The wrapper is `{run_id,matrix_generation,assessment_generation,envelope,
registry_derivation,dispatch_performed:false,runtime_verified:false}`. Unknown
prices/balances/replay coverage and partial-budget conditions remain explicit.
Current-row oversize diagnostics do not claim every future typed inventory result
will fit; every later request still requires individual dispatch preflight.

CLI `run-envelope --run R [--method B0a|B0b|F1|F2] [--origin MODE]
[--config POLICY.json] [--approval DECLARED.json] [--budget-context SNAPSHOT.json]`
has JSON/readable output and no live-enable action. Default method is always B0a.
Web preview uses server-owned context only. None of these bounds or source checks
constitute measured usage, model quality, live authorization or runtime acceptance.
# Policy currentness and explicit reassessment

`ReviewStore(state_dir, *, policy_provider=None)` accepts an optional **trusted
application-injected** actual judgment port for current provider/model/SDK policy
comparison. It never collects semantic fingerprints in construction and never
infers a provider from request text or a stored model name. Typed currentness
without that actual port is `missing`; keyless B0a requires no optional SDK.

`store.policy_status(run_id)` returns run/matrix/assessment identifiers,
`policy_status` and `runtime_verified:false`. `policy_status` contains actual
explicit-operation source status, per-row status, overall `allows_current_claim`,
fingerprints/reasons/action, and `execution_verified:false`. New application
get_run/list_rows/get_row/inventory views carry derived snapshot/row.policy_status;
stored rows, approval events, content revision and decision CAS remain unchanged.
Approval queues exclude policy-invalid positives; stale/unresolved queues include
them. `current_approval` is a presentation label, never a second writable truth.
Positive/gap decisions and current approval graph intents recheck this authority.
Pending graph intents remain pending on policy failure; no checkpoint is opened
to revive an outdated approval. Exact decision retry returns the original event,
not a fresh current approval. History source/evidence views resolve original
literal integrity explicitly; this does not prove current normalized semantics.

Actual commands:

```
policy-status --run R
reassess --run R --matrix-generation SHA --request-id ID --reason TEXT
         [--method B0a|B0b|F1|F2]
         [--provider-config NONSECRET_POLICY] [--allow-live]
         [--replay-tape EXACT_TAPE]
         [--run-envelope INNER_ENVELOPE_JSON --run-approval APPROVAL_JSON]
```

Default reassessment is B0a. Live factory admission remains disabled/gated;
`--allow-live` alone is not authorization or an envelope. Disabled/unavailable
typed assessment stays needs-review; no fallback live calls occur. Existing
replay policy must be disabled and exact, never automatically rekeyed. Reassessment
pins the actual method/port metadata in its idempotent intent, checks matrix CAS
before computation and again at atomic commit, invokes genuine reassess_rows for
all active obligations, preserves manual interpretations and human_origin,
preserves retired history and original machine inventory report, and invalidates
all active approvals without transferring approval to new evidence. The complete
result and actor-resolved event share a revision_requests transaction. Use this
named path after pending human inventory promotion to obtain actual evidence
assessment before explicit row review; promotion itself never assesses/approves.

Final factory reconciliation: future enabled CLI operations require both exact
inner envelope and matching explicit approval objects (the `data.envelope` value
from run-envelope's command response, not the outer response wire). Collection of
the actual semantic descriptor happens explicitly in that CLI operation, never
inside factory construction. The factory validates supplied descriptor/config and
whole-run bounds purely; its later admitted dispatcher independently rechecks
source/process state before SDK/key/DB use. No pins or approval are invented.
These flags are also available on existing provider-enabled import/revise/update/
coverage reassessment parsers; disabled policy and replay reject live-envelope
mixing. Bootstrap a run with B0a/disabled policy, preview its actual registry using
run-envelope and an exact typed config, then use explicit reassess with separately
approved envelope/approval in a later authorized normal-mode run. This avoids
requiring live inference to obtain the initial run's preflight counts. No such
live approval, configuration, collection or dispatch was exercised in this run.

Parser/artifact policy mismatch needs genuine `update-source --run R --pdf PDF
--terms TEXT --matrix-generation SHA [--method B0a]` reimport/reparse; an evidence
reassessment cannot repair source provenance by relabelling old coordinates.
Process source drift requires safe restart before reimport/reassessment; no
watcher, reload or current-status cache is installed. New source/policy updates
leave dependent approvals stale. Local current-policy row edits retain unrelated
content/approvals and the separate opaque decision CAS contract.

New export policy is `m3.complete-inventory.policy.v4`; legacy, coverage.v2 and
audit.v3 intent rendering branches remain intact. V4 freezes explicit policy_review
and render descriptor, adds policy_status/action columns, suppresses positive/gap
commitment fields when currentness is missing/legacy/mismatch/restart-required,
and labels the historical stored approval. Return/download currentness rechecks
actual current context, separately from immutable matrix generation. Prior receipt
bytes remain unchanged; literal/history downloads stay explicitly labelled.
Structural bundle verification and backup integrity use original hashes rather
than running new semantic classifiers over old records. Diagnostics reports
domain integrity separately from current policy; neither is proof of executed
assessment, authenticity, native security, semantic recall or product acceptance.
