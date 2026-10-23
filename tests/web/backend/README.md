# Backend regression definitions — UNRUN

No test, fake application, ASGI request, renderer, authority/challenge, cookie,
server, browser, real DB or provider has been executed during construction.
These files have only been AST-parsed and statically inspected.

`test_security.py` uses pure local session/boundary definitions with hand-built
ASGI messages. `test_routes.py` defines future in-process HTTPX/ASGI scenarios
against a fake application, without a listener or real store. Optional `web` and
provisional `dev` extras must be installed/resolved in a separately authorized
normal-mode validation phase. No current source check establishes dependency or
browser-cookie compatibility.

Each case records `what_bug_this_catches`. Coverage includes auth denial,
one-use challenge/expiry/logout, Host/Origin/port/proxy rejection, CSRF,
cross-run grants, actor/path/provider injection, bounds/duplicates/form conversion,
source revision races and verified historical download labels. Fake application
success only exercises adapter contracts; actual SQLite/CAS/export/auth delivery
and hostile browser behavior require later integrated independent acceptance.

The test-only port is an ASGI scope string. It is never reserved, bound or served.
No test fixture provides a real credential, provider budget, corpus or custody
authorization. Secure local challenge delivery, especially Windows owner-only
privacy and refusal when unavailable, remains D6's separately authored boundary.

Amendment cases added under P4.8 cover scoped historical reads, closed generation
queries, actor/span/approval injection rejection, stale/unknown row/revision
conflicts, form relation/reference bounds and distinct idempotent human lineage
with no approval carry. Their fake application models error propagation and
adapter separation only; actual historical-store membership/CAS remains D6's
runtime and domain-regression obligation. No fake success is a domain acceptance.

Four inventory/preflight definitions under P4.10 add scoped read/promotion,
Origin/CSRF/closed actor/span/approval rejection, stale/unknown/duplicate mapping,
idempotent pending-only promotion, immutable machine-audit counts, independent
typed flags and server-owned preview policy. Domain fake success never proves
actual promotion persistence or semantic recall; tests remain unrun.
