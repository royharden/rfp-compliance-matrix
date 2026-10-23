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
