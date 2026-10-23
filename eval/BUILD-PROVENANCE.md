# Frozen build metadata (source only)

No collector, parser, comparison, template renderer or test was executed during
construction. `eval.build_provenance.collect_build_provenance` is an explicit
future operation. `NativeBuildObserver` construction only stores parser profiles;
its `observe()` explicitly collects native descriptors. No watcher or hot reload
is installed.

The adapter preserves D5's full normalized `policy_context` for each method,
including its verified D4 semantic descriptor and context SHA. Render provenance
uses the separate native render descriptor. Native `descriptor_sha256` protects
the closed outer record; stable `fingerprint` hashes profile plus identity. Process
restart/status/reasons do not become stable semantic identity. The evaluation
member's `descriptor_sha` additionally hashes the entire received descriptor for
the frozen receipt. Neither digest is authenticity, execution or model quality.
Optional distribution metadata remains available/version or unavailable/null.
No absent SDK/model/source is upgraded to verified compatibility.

Pass the collected object as `build_provenance` to `freeze_comparison`, then supply
an explicit `build_observer` to `run_comparison`. The prospective-fake-only-v3
freeze seals full normalized contexts, render metadata, original inputs, method
configuration, external pins and native envelope commitments. The runner checks
active semantic and render pins before external seal consumption, after prediction,
before scoring and before receipt publication. A changed/missing pinned component
or restart-required descriptor blocks the sealed run. A consumed seal remains
spent if later drift aborts. Failures expose no gold or scorer exception text.
Actual pipeline traces must match frozen inventory/assessment fingerprints;
B0b uses F1 inventory and its own assessment context. Its shared extraction cache
also binds the actual normalized inventory context.

An omitted build descriptor is explicitly uncollected/legacy-unverified and can
only exercise the existing fake protocol; it establishes no current integrity
claim. Historical v2 freezes remain immutable and are refused for new dispatch;
create a new explicit freeze and external authorization. Replay v1 history is
never auto-rekeyed into current proof. Model/config/SDK/price/corpus and external
source/scorer pins retain their separate authority obligations: the native
allowlist is not a complete attestation of the evaluation host or custodian.

Receipts retain full build origin, separate semantic/render status, false runtime
and quality claims, and a digest over the entire public receipt. Human authority,
lawful corpus, durable custody, prices, real runtime behavior and independent
product acceptance remain unverified. Current product approval is computed by the
trusted application using D5 policy helpers and actual typed provider configuration;
a captured `status=current` string or this evaluation metadata cannot authorize it.
