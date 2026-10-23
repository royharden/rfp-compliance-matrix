# Local review workflow

The CLI operates on one local reviewer workspace. Runtime state defaults to `%LOCALAPPDATA%/rfp-matrix` on Windows or `$XDG_DATA_HOME/rfp-matrix` (falling back to `~/.local/share`) elsewhere. An explicit `--state-dir` must also remain outside OneDrive. The application does not start a listener, browser or worker daemon. Document and model text is treated as data; it cannot issue decisions or shell commands.

This source was built under Dirty Mode: application commands, imports, parser calls, SQLite operations, LangGraph interrupts and export generation have not been executed. The commands below describe intended future use after dependency/environment activation and validation are authorized.

1. Import a rights-permitted text PDF with `rfp-matrix import <pdf> --vendor <fictional.jsonl> --run demo --terms '<permitted-use terms>'`. Inspect the returned source hash, parser version, coverage warnings and row count. Public hosting is not permission. Corpus records must explicitly be fictional.
2. Run `rfp-matrix review --run demo` to see all rows and their independent mandatory/conditional/administrative flags, evidence status and approval. `review --run demo --row <id>` shows source pointers, support and counterevidence, template, uncertainty and exact generations. `source --run demo --row <id>` resolves the literal source interval and displays surrounding context plus frozen fictional evidence. `inventory --run demo` displays the persisted original classification/discard report; its source anchors and reasons survive restart without repeating provider calls.
3. Use `review --run demo --interactive` for terminal review. Enter/S opens source; A asks for an explicit human validation attestation before positive approval; G acknowledges an absent/conflicting gap; E edits into a pending unvalidated revision; R rejects with reason; D defers; N records not-applicable rationale; I edits the requirement interpretation; C changes applicability; X splits; M merges; Q leaves. Split/merge retains literal anchors and recorded ancestry, and requires rationale. Changed requirements return to pending; unaffected exact approvals remain bound to their prior valid revision. The graph presentation provides a stable request ID when durable checkpoints are available. Checkpoint unavailability is displayed explicitly and does not masquerade as resumed graph execution.
4. For scripted review use `decide --run demo --row <id> --action <action> --assessment-generation <displayed> --row-revision <displayed> --request-id <stable-id> --reason '<rationale>'`. `--human-validated` is an explicit attestation only after inspecting the exact restricted template and evidence. It cannot approve edited free prose, unknown applicability, incomplete coverage, unavailable assessment, partial support or contradiction. Repeating the same key/payload is idempotent; a changed payload conflicts.
5. Exit and invoke `resume --run demo`. Domain decisions and synchronous resume intents are authoritative; real LangGraph checkpoint recovery remains a future runtime check. `history --run demo` displays committed events. No status here is a legal-compliance finding or a vendor commitment.
6. Export using `export --run demo --out <directory> --matrix-generation <displayed> --request-id <stable-id>` (all formats by default; `--format html|csv|md` selects one). All rows remain visible. Positive approval, gap acknowledgment, rejected/pending/stale rows and original event references stay distinct. `verify-export <bundle>` is intended to verify deterministic artifact hashes.
7. `update-evidence <vendor-v2.jsonl> --run demo --matrix-generation <displayed>` recomputes evidence for active human requirement versions, preserving their interpretation/split/merge lineage and original inventory report, and invalidates corpus-dependent approvals. `review --run demo --queue stale` shows only stale rows. Old exports remain historical; fresh approvals require current generations. `update-source <pdf> --run demo --matrix-generation <displayed> --terms '<terms>'` intentionally rebuilds inventory and its report for the new artifact and applies the corresponding source-change boundary.

For scripted requirement changes use `revise --run demo --operation interpretation --row <id> --interpretation '<new interpretation>' --reason '<rationale>' --matrix-generation <displayed> --request-id <stable-id>`. For a split repeat `--interpretation` for each child; for a merge repeat `--row` and supply one merged interpretation. Applicability uses `--operation applicability --applicability applies|unknown|not_applies`. Original rows and decision history remain preserved; retired rows cannot be reapproved. Raw user intent is persisted with the revision request, so an exact retry can return the original result before attempting another revision, while changed intent conflicts.

The non-model B0a arm is the default keyless construction path. B0b, F1 and F2 are explicit methods; unavailable providers remain unavailable, with no fabricated live distributions, cost or successful absence. Lazy live SDK/HTTP adapter construction is now present in `providers/`, with explicit `ProviderConfig` provenance/admission and disabled zero-budget defaults. `create_provider(config, client=..., http_client=..., ledger=...)` remains disabled without admission and a bounded ledger/dispatcher. Adapter source is not installed compatibility, verified transport behavior or permission to call a model. F2 generated classifications are not calibrated Jev distributions. See `providers/README.md` and `providers/PROVENANCE.md` for the owned provider details.

Human coverage decisions are separate from immutable parser artifacts. The application-owned `ReviewStore.coverage_state(run_id)` returns current page revisions; `inspect_coverage(run_id, page_index, action, expected_artifact_sha=..., expected_page_hash=..., expected_matrix_generation=..., expected_coverage_revision=..., request_id=..., reason=..., inspected_original=...)` records the internally resolved actor and exact CAS/idempotency intent. Source-owned pure policy functions cannot authenticate a reviewer. Stale or hard-unsupported image/error states cannot become complete extraction by declaration. Inspection changes invalidate dependent approvals. Maintenance source provides `ReviewStore.backup(destination)`, `diagnostics()` and `review.operations.restore_store(bundle, destination)`; restoration targets a new directory and treats unpinned checkpoints explicitly. CLI exposure is owned by the CLI implementation; use its final source/help rather than guessing flags. No operation described here was executed during construction.

No live provider spend is authorized and the budget is zero. Public quality evaluation still requires approved lawful families, humans, independent sealed custody, pinned SDK/model/license/price policy, admitted transport, authorized live comparisons and nonauthoring product acceptance. [Architecture](ARCHITECTURE.md), [activation](ACTIVATION.md), [demo](DEMO.md) and [corpus workflow](../eval/CORPUS-WORKFLOW.md) distinguish implemented source from those unverified obligations.

Current CLI source exposes `coverage --run R [--page 1|--interactive]` and TUI V.
For an exact decision use `inspect-coverage --run R --page 1 --action
accept_blank|accept_text|flag_unresolved --artifact-sha A --page-sha P
--matrix-generation G [--coverage-revision V] --request-id C --reason '<reason>'
[--inspected-original]`. Enter the chosen action and exact displayed identities
on one command line. `reassess-coverage --run R --matrix-generation G
--request-id V --reason '<reason>' [--method B0a]` creates pending reassessment,
never approval. Diagnostics accepts `--run R`; maintenance is `backup --out NEW`
and `restore BUNDLE --destination NEW_LOCAL`. Restore never overwrites a workspace.

`provider-status --config <nonsecret-policy.json>` performs no dispatch. Current
import/revise/update/reassess paths accept `--provider-config`, with a separate
`--allow-live` guard. Flags and policy files do not authorize calls: real admission,
exact model/SDK/price/source/corpus bindings and budget approval remain required
and absent. Provider replay/stale helpers remain evolving source. No command
described here was executed during construction.

Policy upgrade source APIs (UNRUN):

```text
rfp-matrix policy-status --run R
rfp-matrix reassess --run R --matrix-generation SHA --request-id ID --reason TEXT [--method B0a]
```

Reassessment genuinely recomputes all active evidence, preserves manual origin and
retired history, and invalidates active approvals. A parser/artifact policy mismatch
requires actual update-source/reimport. Drift requires safe restart. Typed currentness
requires the trusted application's actual injected provider port, never a stored
model label. Disabled/unavailable typed paths remain needs-review; B0a is keyless.
Future enabled provider commands also require exact --run-envelope INNER.json and
--run-approval APPROVAL.json, plus existing admission. INNER is data.envelope from
the run-envelope response, not the whole JSON command envelope. No live approval
exists here. Replay requires disabled matching policy and rejects live-envelope
mixing; legacy replay history is not rekeyed. See review/API.md in src/rfp_matrix
for the owning API contract.
