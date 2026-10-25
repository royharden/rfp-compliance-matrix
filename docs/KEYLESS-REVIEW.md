# Keyless local review

Use Python 3.12 or newer and the project's locked `test` extra for the complete
parser, graph, web and test dependencies. The baseline workflow needs no API key
or provider SDK. Keep the state directory outside OneDrive. The examples below
use PowerShell and assume `rfp-matrix` is installed in the active environment.

Generate a new, explicitly fictional development pack:

```powershell
python scripts/demo.py --out C:\M3-local\inputs
rfp-matrix --state-dir C:\M3-local\state import C:\M3-local\inputs\fictional-rfp.pdf --vendor C:\M3-local\inputs\vendor-v1.jsonl --run demo --terms 'Project-authored fictional development example'
rfp-matrix --state-dir C:\M3-local\state review --run demo
```

Choose a new output directory for the generator; it preserves existing inputs.
These generated inputs are synthetic development cases, not a permitted public
RFP corpus, human labels, or evidence of model quality. Inventory counts are
observations, not an independently established requirement recall score.

Commands return versioned JSON envelopes by default. Successful output is on
stdout; a refused command returns a nonzero exit code and its JSON error envelope
on stderr. In particular, stale-generation refusal returns exit code 2 and status
`conflict`. Automation must preserve both streams and check the exit code before
parsing a success result. `--output text` is a global option and precedes the
command. Interactive review always displays text.

Open a displayed row with its exact ID:

```powershell
rfp-matrix --state-dir C:\M3-local\state --output text review --run demo --row ROW_ID
rfp-matrix --state-dir C:\M3-local\state source --run demo --row ROW_ID
rfp-matrix --state-dir C:\M3-local\state review --run demo --row ROW_ID --interactive
```

Interactive review opens a durable graph presentation. `S` shows source/context,
`G` acknowledges a gap, `E` records an unvalidated pending edit, and `Q` leaves.
Review the exact evidence and reason before deciding. A baseline similarity match
does not establish full semantic support; a partial row cannot become a positive
commitment merely because its text looks similar. A gap acknowledgment preserves
the gap and does not promise vendor compliance.

For explicit commands, copy `assessment_generation` and the row's opaque
`decision_revision` from the current review response. Pass the latter through
`--row-revision`; `row_revision` remains the separate content identity.

```powershell
rfp-matrix --state-dir C:\M3-local\state decide --run demo --row ROW_ID --action accept_gap --assessment-generation ASSESSMENT_GENERATION --row-revision DECISION_REVISION --request-id gap-001 --reason 'Reviewed the absent evidence in the fictional corpus'
rfp-matrix --state-dir C:\M3-local\state resume --run demo
rfp-matrix --state-dir C:\M3-local\state history --run demo
rfp-matrix --state-dir C:\M3-local\state review --run demo
```

`accept_gap` applies to an absent/conflicting row. Use a stable request ID for an
exact retry with the same original payload and revisions. A different action or
payload needs a new request ID and refreshed versions. Exiting and starting these
commands in another process preserves the decision in the application ledger;
checkpoint state cannot manufacture an approval.

Export with the latest matrix generation returned after the decision:

```powershell
rfp-matrix --state-dir C:\M3-local\state export --run demo --out C:\M3-local\exports --matrix-generation MATRIX_GENERATION --request-id export-001
rfp-matrix verify-export BUNDLE_DIR
```

The export response supplies `bundle_dir`. The bundle contains readable HTML,
CSV, Markdown, JSON lineage and a manifest. Unresolved rows remain visible. Exact
export retries return the original export and bytes. `verify-export` checks
artifact integrity; its `structurally_verified` result does not establish current
approval against a store or semantic correctness.

Update the fictional corpus with the matrix generation current before the update:

```powershell
rfp-matrix --state-dir C:\M3-local\state update-evidence C:\M3-local\inputs\vendor-v2.jsonl --run demo --matrix-generation MATRIX_GENERATION
rfp-matrix --state-dir C:\M3-local\state review --run demo --queue stale
```

The changed corpus invalidates dependent approvals. Exporting with the old matrix
generation is refused; previous bundles remain historical. Inspect the new
evidence and approve the new exact revisions explicitly before making a current
approved commitment. See [typed review](TYPED-REVIEW.md) for separately bound typed
policy identities and [browser review](BROWSER-REVIEW.md) for the optional local
session boundary. No listener is needed for this CLI journey.
