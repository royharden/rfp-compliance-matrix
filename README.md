# RFP compliance matrix

A local Python review tool for native-text PDF RFPs and an explicitly fictional vendor evidence corpus. It preserves literal source spans, distinguishes support from counterevidence and gaps, records explicit human decisions, and prepares a complete Markdown/CSV/HTML matrix with decision lineage.

**Construction source — runtime unverified.** Built under Roy's Dirty Mode instruction. Only source inspection and syntax parsing are permitted in this phase. The application, dependency imports, PDF parser, SQLite/LangGraph restart path and exports have not run. No model-quality, speed, cost, security or release-readiness claim is made. No service, account, deployment or remote repository is needed for the intended local CLI.

## What is implemented in source

- Native-text PDF import, immutable source identity and original Unicode-codepoint anchors; per-page extraction coverage warnings.
- Structural requirement inventory, BM25 candidates, conservative evidence/contradiction handling and typed unavailable provider outcomes. Injected mock/replay typed-inventory and registered-ID judgment ports exist; live SDK/HTTP wrappers and a transport concurrency/deadline controller are absent and unadmitted. They are source work still to implement, not merely untested integrations.
- Local durable review state with exact-generation decisions, explicit positive/gap approvals, pending edits, history and corpus-wide stale approvals.
- CLI/TUI source/context review with versioned interpretation/applicability/split/merge controls, real LangGraph checkpoint bridge source, synchronous decision-intent reconciliation and deterministic export/recovery source.
- Public development scoring with one-to-one adjudicated atom matching, explicit denominators, mandatory misses, retrieval/citation/abstention metrics, family-bootstrap summaries, full/preliminary dataset-count eligibility and four method rows.

## Intended use after runtime validation is authorized

The intended later activation command is `uv sync --extra graph`, which includes the real LangGraph SQLite-checkpoint dependencies. Dependency resolution, lockfile compatibility and required license admission are deferred; no installs were performed during construction. Use the `rfp-matrix` entry point or `python -m rfp_matrix` in the activated environment.

```text
rfp-matrix diagnostics
rfp-matrix import <permitted-text.pdf> --vendor <fictional.jsonl> --run demo --terms "<permitted-use terms>"
rfp-matrix review --run demo --interactive
rfp-matrix resume --run demo
rfp-matrix export --run demo --out <directory> --matrix-generation <displayed-generation> --request-id <stable-id>
rfp-matrix update-evidence <vendor-v2.jsonl> --run demo --matrix-generation <displayed-generation>
rfp-matrix review --run demo --queue stale
rfp-matrix verify-export <bundle>
```

These commands are intended interfaces, not executed evidence. Runtime data defaults to a machine-local directory outside OneDrive; `--state-dir` precedes the subcommand. Source terms and hashes remain visible. JSONL vendor records use `{record_id, text, terms, fictional: true, source_url?}`; use fictional information only. The interactive reviewer asks separately before human validation. Similarity, provider confidence, an edit or a gap acknowledgment cannot create a positive commitment. Source/corpus changes invalidate approvals; old exports are historical.

[Workflow](docs/REVIEW-WORKFLOW.md) explains decisions, generation conflicts and source navigation. [Synthetic example](demo/README.md) describes an authored development-only fixture generator that has not run. [Development scoring](eval/README.md) documents the hand arithmetic oracle and explicit zero-denominator N/A behavior; no scoring run has been executed. [Comparison status](results/results.md) retains all four required arms as not yet measured.

## Deferred validation and limits

This source was written by Codex agents and accepted from their focused static inspection and syntax reports. No unit, integration or end-to-end tests ran. Dependency ranges are provisional, with no resolved lockfile or installed compatibility evidence. Table/image/sparse-page coverage stays visibly incomplete; no human coverage-override workflow is implemented.

After Dirty Mode closes: resolve/install compatible licensed dependencies, exercise actual PDF/SQLite/LangGraph/CLI/export paths, restart and concurrency failures, stale-generation races, pointer validity and injection controls. Before any live provider work, implement/admit the missing transport, pin exact SDK/model/license/price policy and authorize a budget; the current live budget is zero. Lawful public-PDF selection, human annotation/adjudication, independent sealed held-out custody, authorized live B0a/B0b/F1/F2 comparisons and independent nonauthoring acceptance remain required before quality claims. No API keys, paid calls, real vendor secrets or real accounts were used. Scans/OCR, browser UI, proposal submission, autonomous commitments, cloud deployment and legal advice are outside this construction scope.
