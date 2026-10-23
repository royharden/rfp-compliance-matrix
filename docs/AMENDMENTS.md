# Amendment correspondence — source only

The optional comparison uses historical snapshots of the same owned run. Original
RFP and fictional evidence generations remain immutable. Source/corpus changes
invalidate dependent approvals; a human correspondence records lineage only and
never carries a positive answer, gap acknowledgment or approval into a new source.
No comparison, mutation, renderer, browser, database or test was run in construction.

`evidence.amendments.amendment_candidates(old_snapshot, new_snapshot, limit=200)`
proposes bounded lexical correspondences between registered original-source rows.
Literal excerpts, dates/conditions, independent mandatory/conditional/administrative
flags, applicability, hashes and split/merge ambiguity remain visible. Token overlap
is a retrieval score, not semantic equivalence. Unmapped mandatory additions and
removals require explicit review; a truncated/incomplete candidate set is not a
complete amendment inventory. Human correspondence events remain separate from
these proposals. Unknown or ambiguous ancestry stays unknown.

The application source provides `amendments(run_id,
previous_matrix_generation=...)` from owned history and
`record_correspondence(run_id, payload)` for exact-version, internally attributed
events. Backend GET `/runs/{run_id}/amendments` optionally takes only the exact
64-character `previous_matrix_generation`; POST
`/runs/{run_id}/amendments/correspondence` accepts the closed fields below plus
the route-issued CSRF token. No filename, path, URL, arbitrary source span, actor,
provider policy or approval action is accepted.

| Field | Meaning |
|---|---|
| relation | one_to_one, split, merge, added or removed |
| old_rows / new_rows | Registered `{row_id, row_revision}` lists encoded as JSON in form values |
| expected_old_matrix_generation | Exact previous matrix snapshot |
| expected_matrix_generation | Exact current matrix snapshot |
| expected_old_assessment_generation / expected_new_assessment_generation | Exact old/new assessment identities |
| expected_old_artifact_sha / expected_new_artifact_sha | Exact original extraction identities |
| request_id | Route-issued stable request identity |
| expected_correspondence_revision | Current lineage-log revision; empty form value becomes null for first event |
| reason | Explicit human rationale, required |

The domain rechecks owned-run membership, relation cardinality, row revisions,
all generations, idempotency and authorization at commit time. Repeating the same
request/payload is intended to replay its event; a changed payload or stale source
must conflict. A correspondence success changes no answer/decision approval.
Browser source permits only registered selections, not arbitrary JSON prose.
The page offers individual registered rows and bounded lexical-neighbor groups
for split/merge selection. It shows exact revisions and sends each list using
JSON serialization followed by attribute escaping; no trusted-markup bypass is
used. Group proposals remain unconfirmed and the domain validates cardinality
and current membership again. Unsupported/missing comparison state removes forms.
Old/current inventories and unmapped mandatory lists remain readable when no
proposals are available. Human history is separate; historical events cannot
confirm other candidates or fill approved answers.

Future activation must validate changed dates/conditions, repeated clauses,
ambiguous splits/merges, newly mandatory omissions, cross-artifact/current-generation
races, unknown/retired IDs, exact retry versus changed payload, history/restart,
autoescaping and no approval carry. Actual CLI comparison/confirmation commands
follow D6's accepted source/help; do not invent command names or generations.
The original public corpus/model-quality requirements and unmeasured results remain.

## Actual CLI source

The current CLI defines these intended commands; none were executed here:

```text
rfp-matrix --output text amendments --run demo --previous-matrix-generation <owned-old-matrix>
rfp-matrix correspondence-history --run demo --limit 100
rfp-matrix confirm-correspondence --run demo --relation one_to_one --old-row <old-id>=<content-revision> --new-row <new-id>=<content-revision> --old-matrix-generation <old-matrix> --matrix-generation <current-matrix> --old-assessment-generation <old-assessment> --new-assessment-generation <new-assessment> --old-artifact-sha <old-artifact> --new-artifact-sha <new-artifact> --request-id <stable-id> --reason '<human-rationale>'
```

For later lineage events add `--correspondence-revision <last-committed-revision>`;
omit it only before the first event. Repeat `--old-row`/`--new-row` for registered
split/merge selections. Row references carry content revisions, not decision-CAS
tokens. Choose the relation with the correct nonempty/empty side and cardinality.
Version values come from the displayed owned comparison, never guessed hashes.
Default output is the versioned JSON envelope; `--output text` is a global option.
Conflicts must expose current identities and require fresh inspection. These
source commands do not authorize a real mutation during Dirty Mode.
