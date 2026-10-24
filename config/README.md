# Provider configuration

`provider.disabled.json` is a direct `ProviderConfig.from_mapping` input, with no
credentials and zero live budget. Its public SDK provenance does not authorize
service use. It is safe to construct; do not call live providers in Dirty Mode.

The optional `typesafe` extra declares the publicly inspected exact SDK 0.7.5.
The `dev` pytest range is provisional metadata for later test execution. Neither
extra has been installed/resolved, no lockfile is claimed, and transitive
licenses/API compatibility remain unverified. OpenAI-compatible HTTP uses stdlib.

For later authorized activation, use the API and policy fields documented in
`src/rfp_matrix/providers/README.md`. Resolve missing terms/model/prices and exact
source/corpus bindings, validate the token-bound assumptions, obtain real budget
authorization, then record the resulting config hash and admission reference.
Never set admission fields merely to make an example run. Keys are environment
variable names only and never belong in this file. Use one shared machine-local
ledger across clients/processes. No moving model alias or invented F2 probability
distribution is permitted.

`semantic_fingerprint` defaults to the empty string; this is an explicit missing
binding and cannot enable future live calls. Configuration construction never
collects source or metadata. For a future authorized real-live factory, supply
the full verified `semantic_provenance` descriptor as well as the matching
full-run envelope and prospective record. A hash alone is insufficient. Actual
judge/attempt checks current process/source before key, SDK or ledger dispatch.
Changed semantics require a fresh context/config/envelope and genuine
reassessment; old replay/evidence cannot be rebound to a new fingerprint.
Process source drift requires restart. Descriptor integrity is not execution,
account authorization, license clearance or security attestation.

The keyless example remains disabled with zero budget. No descriptor, real
approval, source collector output, credential or measured price is embedded.

For a later review session, the separately supplied nonsecret
`--review-policy-config` input (CLI owner) targets
`review_policy_identity(config, semantic_provenance=actual_current_descriptor)`.
Use disabled/zero budget and empty admission/approval fields; retain exact
method/model/SDK/question/source/document/artifact/corpus/semantic identities.
No model request, ledger or live approval is needed for this read-only handle.
An unavailable/restart-required handle cannot establish current policy. Its
presence cannot refresh evidence, transfer approval or bypass decision CAS.
It cannot serve as an inference/evaluation port. Actual command coverage and
per-run binding are owned by the CLI/application, separately from this factory.
