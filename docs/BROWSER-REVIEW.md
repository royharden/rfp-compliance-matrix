# Optional local browser reviewer — source only

The selected optional reviewer now has backend/session, presentation, application,
template and local CSS source. It has not been rendered, started, opened in a
browser or security-tested. CLI/TUI remains the minimum complete workflow; import
stays in the CLI. No upload, provider policy selection, actor selection or arbitrary
filesystem destination is accepted by the browser forms.

Future activation requires admitted optional web dependencies (`web` extra), real
runtime checks and an approved agent-ports claim/check before a loopback listener.
The source factory is `web.create_app(application, port=..., authority=...)`;
constructing source is not authorization to serve. The CLI source accepts
`serve --port <claimed> --port-claim-reference <receipt> --run <existing-run>`;
secure challenge delivery and OS privacy policy must be validated before use.
The actual private bootstrap flag is `--private-login-delivery`. It requires
verified owner-only file delivery and fails closed where privacy cannot be
established; it must not print a capability to terminal/stdout/stderr/log/URL.
The earlier `--bootstrap-console` alternative is superseded and not active.
Never place a session/challenge/API credential in a URL, log, screenshot or source
document. Missing private delivery or authentication must fail closed.

The source route contract is:

| Page / action | Route | Boundary |
|---|---|---|
| Authorized workspaces | GET `/` | Session-authorized runs only |
| Sign in | GET/POST `/login` | POST credential + csrf_token; password field never echoes input |
| Sign out | POST `/logout` | csrf_token only |
| Matrix / queue | GET `/runs/{run_id}` | Optional fixed queue selector |
| Source-linked row | GET `/runs/{run_id}/rows/{row_id}` | Exact snapshot/context generation |
| Decide | POST `/runs/{run_id}/rows/{row_id}/decide` | Exact assessment/row versions, request ID, CSRF |
| Coverage | GET `/runs/{run_id}/coverage` | All page states and audit revisions |
| Inspect page | POST `/runs/{run_id}/coverage/inspect` | Original page/artifact/matrix/CAS identities |
| Export | POST `/runs/{run_id}/export` | Exact matrix generation; HTML/CSV/Markdown, no path |
| Export history | GET `/runs/{run_id}/exports` | Committed artifacts, historical label |
| Committed download | GET `/runs/{run_id}/export-files/{export_id}/{filename}` | Fixed file allowlist, verified bytes/currentness |

Decision fields are `action, expected_assessment_generation, expected_row_revision,
request_id`, optional `reason, answer_text, human_validated`, and route CSRF. Actor
comes from the trusted application, never the form. Positive approval requires a
separate unchecked human-validation attestation. Edit saves pending; acknowledge
gap cannot populate approved commitment. Stale/retired/missing-version rows have
no mutation controls. Domain policy and CAS remain authoritative on POST.
The form's expected_row_revision is the presenter controls value for the exact
decision-CAS revision. Displayed content row_revision remains the content/approval/
amendment identity; no extra HTTP revision field is introduced. Present-invalid
decision tokens disable controls, and only legacy-absent tokens may use the
documented domain fallback. A row decision cannot be silently overwritten merely
because its content revision is unchanged.

Coverage fields are zero-based `page_index, action, expected_artifact_sha,
expected_page_hash, expected_matrix_generation, expected_coverage_revision,
request_id, reason`, optional `inspected_original`, and route CSRF. Empty first
revision becomes null. Display pages are one-based. Unsupported/image/error/unknown
issues expose unresolved-only action; native/blank acceptance still requires actual
original-page inspection and source policy validation. A page acknowledgment does
not perform OCR or prove extraction quality.

Backend supplies `request, vm, csrf_token, request_id, runtime_unverified` to Jinja
with autoescape and StrictUndefined. Templates use D5 row/requirement/evidence/
controls/lineage/notes/context/coverage/changes mappings. Quotes are text, never
trusted markup. Links have fixed local route prefixes and encoded application IDs;
source URLs or provider/model strings cannot supply href/action values. Export
links use constant allowed filenames, not an incoming URL. No JavaScript, remote
font, CDN or external asset is required. CSS includes keyboard focus, skip link,
semantic labels and readable responsive layouts; accessibility remains unverified.

The current CLI defaults to a versioned JSON envelope (`--output json`); use
`--output text` for readable noninteractive output. Interactive TUI is text.
Keyless replay requires both disabled matching `--provider-config` and
`--replay-tape`, and forbids `--allow-live`. Tape integrity is not authenticity;
origin stays replay and cannot establish live quality. All four evaluation arms
remain unrun/unmeasured. Browser session capability never approves provider spend.

The optional [amendment flow](AMENDMENTS.md) is reached from the matrix's Compare
amendments link. Proposed correspondences, human lineage events, before/after
literal context and unmapped mandatory rows remain distinct. Correspondence is
never an approve/reapprove shortcut.

Typed review currentness is supplied only by the trusted application/CLI, per
[TYPED-REVIEW.md](TYPED-REVIEW.md). No new browser policy/config/live-admission
fields or routes are introduced. Policy warnings are display-only; exact current
source/evidence, restricted validation and opaque decision CAS still govern every
POST. A policy identity does not grant run access, expand session grants or revive
a stale approval. Multi-run bindings must be checked separately for each run.
