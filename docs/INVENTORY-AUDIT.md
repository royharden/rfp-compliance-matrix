# Inventory audit and human pending correction

This source-only workflow derives an audit from the immutable machine inventory
report and exact current registry. No audit, promotion, parser, model, renderer,
browser, database or CLI operation was executed during construction.

`evidence.inventory.inventory_audit(artifact, inventory_report)` accounts for
registered candidates and usable structural segments: retained, rejected,
uncertain or unattributed, with original intervals and explicit reasons. Stale
reports, unknown pointers and duplicate/conflicting dispositions fail closed.
Provider absence stays distinct from intentional context exclusion. Unanchored
tables, reading-order uncertainty and page gaps stay visible. Complete attribution
cannot establish semantic recall or that all requirements were found.

Browser GET `/runs/{run_id}/inventory` consumes D5's `inventory_audit_view` from
the application-owned audit/current snapshot. Fixed section links show all four
dispositions, structural gaps, per-page coverage and separate human corrections.
Text is autoescaped; missing/stale identity disables promotion.

POST `/runs/{run_id}/inventory/promote` carries registered candidate_id,
expected_artifact_sha, expected_assessment_generation, expected_matrix_generation,
request_id, human reason and route CSRF. No actor, arbitrary source span, provider
policy, live-enable or approval input exists. Browser promotion preserves original
interpretation/independent flags; explicit optional overrides use the typed
CLI/service contract. Retained/promoted candidates and unanchored gaps are ineligible.

The pure `promote_inventory_candidate` producer prepares a NEW PENDING row with
immutable source lineage, human-origin metadata, needs_review evidence, pending
validation and no positive decision. D6 resolves actor, current policy, CAS,
idempotency, duplicate and row-limit checks and persists the correction event.
Promotion dispatches no provider and never rewrites the original machine report.
Exports retain the added unresolved row/lineage. Human corrections cannot count
as improved blind B0a/B0b/F1/F2 output or model-quality evidence.

Later validation must exercise unknown/repeated/stale candidates, missing/duplicate
dispositions, Unicode/repeated anchors, incomplete provider/parser states, table
gaps, races, duplicate promotion and preservation of unchanged exact approvals
with new pending matrix/export identities. Current source remains unrendered and
runtime unverified. Actual CLI names follow the accepted owner handoff.

Actual noninteractive CLI source (UNRUN; global --output json|text precedes command):

```text
rfp-matrix inventory-audit --run R
rfp-matrix promote-inventory --run R --candidate-id ID --artifact-sha SHA --assessment-generation G --matrix-generation M --request-id C --reason TEXT
rfp-matrix run-envelope --run R [--method B0a|B0b|F1|F2] [--origin MODE] [--config POLICY.json] [--approval DECLARED.json] [--budget-context SNAPSHOT.json]
```

Promotion optional fields are --interpretation, --mandatory true|false,
--conditional true|false, --administrative true|false and --applicability
applies|unknown|not_applies. This creates pending human lineage, never approval.
The default envelope is a zero-provider baseline preview; it dispatches nothing.
Export policy m3.complete-inventory.audit.v3 introduced fixed inventory-audit.json
and promotion provenance. The current m3.complete-inventory.policy.v4 also freezes
application policy review, blanks policy-invalid commitments and retains unresolved
rows. Legacy/v2/v3 intent bytes remain unchanged. The
browser history links only committed allowlisted files; download currentness
and exact bytes remain trusted-application obligations.
