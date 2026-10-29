# Reviewing previously computed typed evidence

Construction only: these workflows and regression definitions have not been run.
No model call, source collector, credential access or inference admission was
exercised. CLI/TUI and the optional browser share trusted application currentness
rules; a browser request cannot provide a model policy or enable inference.

A later review process needs an explicit nonsecret current-policy identity for
previously computed typed work. This is separate from an inference provider. It
compares actual current method/model/SDK/question/semantic identity and exact
source/corpus bindings against the recorded assessment. A matching identity is a
necessary currentness condition, not proof that predictions ran, remain correct
or deserve approval. Restricted human validation and exact decision revisions
still apply. F2 remains uncalibrated, needs review and cannot become an approved
positive answer through a policy match.

The provider API is `review_policy_identity(config, *, semantic_provenance)`,
returning the frozen `ReviewPolicyIdentity` type. It exposes available,
unavailable or restart_required status, a fixed reason code, a restricted identity
mapping and a defensive descriptor copy. Its read_only_policy marker is true;
execution_verified and live_authorization_verified are false. It has no judge or
dispatch method. The pure factory receives an explicitly supplied actual current
descriptor; it does not collect source, install/import an SDK, open keys/ledger/
tapes, consume approval or enable inference. The trusted command performs actual
current collection and per-run binding at the explicit operation boundary.

Identity fields are method, model, sdk_name, sdk_version, question_policy,
source_policy, document_id, source_sha256 (the artifact identity), corpus_sha256
and semantic_fingerprint. The review config must be disabled and zero budget,
without admission_reference or approved_config_sha256. Full config, price,
admission and free-text fields never become browser VM policy input. This is not
a shortcut for supplying a credential or reading a captured assessment as proof.
The pure shared `policy_identity_projection(config)` supplies these exact ten
fields for both genuine-assessment receipts and review handles. It excludes
admission noise from this comparison while preserving the original assessment
policy hash and execution-provider identity separately.

`is_review_policy_identity(value)` is the provider-owned pure exact-type guard.
The closed purpose is `review-policy-identity-only-v1`. Consumers use that nominal
guard before accessing the object; a user-supplied purpose string or arbitrary
marker/config property is not trusted identity.

The optional assessment `review_policy_identity` receipt is captured by the
evidence producer only during a genuine assessment. Its absence in historical
work stays legacy. A later review handle or prospective evaluation freeze cannot
backfill that receipt, rebind the original admission-sensitive configuration hash
or manufacture evidence execution. All original provenance and origins survive.
Its closed wire contains schema_version, purpose, identity, execution_provider,
prediction_origin, reported_model, semantic_identity, engine_policy,
assessment_sha256 and receipt_sha256. The evidence owner binds the receipt to
the actual assessment body and original policy, not to a reconstructed prediction.
Matching the stable projection alone is insufficient: independently supplied
current descriptors, exact artifact/corpus bindings, original policy/receipt
integrity and actual reported live model/SDK semantics still apply. Mock/replay/
unavailable origin cannot match a later live-policy review claim.

The actual CLI source accepts repeatable `--review-policy-config FILE` only on
review (including interactive TUI), decide, resume, export, policy-status and
serve. It accepts at most 32 distinct run/method bindings. Examples below are
UNRUN and require separately admitted normal-mode dependencies/state:

```text
rfp-matrix review --run R --review-policy-config R-F1.json
rfp-matrix review --run R --interactive --review-policy-config R-F1.json
rfp-matrix policy-status --run R --review-policy-config R-F1.json
rfp-matrix resume --run R --review-policy-config R-F1.json
rfp-matrix decide --run R --row ROW --action defer --assessment-generation G --row-revision CAS --request-id C --reason TEXT --review-policy-config R-F1.json
rfp-matrix export --run R --out NEW --matrix-generation M --request-id C --review-policy-config R-F1.json
```

`R-F1.json` means the exact existing run's nonsecret disabled policy, not an
example credential or an automatically generated admission. Use actual reported
artifact/corpus/semantic identity and exact model/SDK/question policy; do not
invent hashes or infer them from an old answer. There is no collection on handle
or binding construction. The explicit command loads the supplied policy and
collects actual current context, then independently resolves each run/method.

The path refuses mixing with --provider-config, --allow-live, --replay-tape,
--run-envelope or --run-approval. It is unavailable on import/reassess/update
commands; actual reassessment uses its separate inference-policy path. Baseline
B0a needs no review-policy config. A wrong artifact/corpus/method, duplicate
binding or out-of-grant run is an error. Missing another run's binding stays
missing rather than falling back to a different document.

For a later explicitly authorized browser session, append a policy file for each
required run/method to the existing `serve --run R [--run S] --port CLAIMED
--port-claim-reference RECEIPT --private-login-delivery` invocation. Separate
session grants, claimed loopback port and verified owner-only bootstrap delivery
remain required. No server or private-file/OS-privacy check ran here.

| State | Reviewer action |
|---|---|
| Missing or malformed current policy | Supply the explicit nonsecret policy through the trusted CLI path; keep historical answers unapproved. No fallback call is permitted. |
| Wrong method/model/SDK/question/source/corpus | Resolve the exact run's policy; if semantics changed, obtain an actual new assessment. Do not rebind old evidence to a new hash. |
| Legacy provenance | Inspect literal history; reimport/reassess under the current policy before current claims. Renaming old replay proof cannot repair it. |
| Restart required | Safely restart before collection and actual reimport/reassessment. Work captured in a drifted process remains noncurrent after restart. |
| Matching current identity | Inspect original source, evidence, applicability, completeness and decision state; then explicitly decide using the displayed CAS token. Matching alone approves nothing. |

Baseline B0a remains keyless without an optional typed SDK. For typed work,
missing required SDK/version metadata remains missing; a configured version string
does not establish actual installed compatibility. Preserve recorded mock/replay
origin. A read-only identity cannot turn simulated or historical predictions into
live measurements or fresh inference.

The trusted application binds policy to each run, artifact and corpus. A multi-run
browser session must fail closed on the wrong document configuration. Forms carry
only code-owned actions, CSRF/request identifiers and exact versions; no actor,
credential, provider configuration, raw policy object or live-admission field is
accepted from a browser. Content row revision and opaque decision CAS remain
distinct. New review identity does not mutate evidence, approvals or history.

Evaluation consumes actual prediction ports and their closed provenance receipts.
A review-only policy handle is never a method prediction port or a successful
judgment delegate. Prospective native build/config identity in a freeze remains
separate from actual mock/replay/baseline prediction trace, nullable measured
usage and independent consumed-seal/scoring authority. An identity comparison
cannot populate results, change origin, refresh a seal or grant spend.
