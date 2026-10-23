# Provider regression source — unrun

These pytest definitions are construction artifacts. They have only been parsed
and inspected. No test, fixture, fake transport, database, clock/worker or app import
has been executed in Dirty Mode. Future execution needs an authorized normal-mode
environment with the declared app/dev dependencies resolved.

Each case names the concrete bug it catches. `conftest.py` supplies test-only
configuration/admission strings, hand-authored SDK and HTTP responses, a local
clock and synchronous worker substitution. No public/live model output, private
gold, account, credential or actual budget authorization is claimed. Temporary
SQLite use and calls to fake clients occur only if tests are run later.

Boundary cases cover disabled construction/no I/O, exact approval binding,
distribution/registered-ID validation, malformed probabilities, F2 raw labels and
refusal usage. Dispatcher cases cover one retry/no hidden retries, deadline,
cancellation-before-reservation, exact cache identities and preflight. Ledger
cases cover atomic two-attempt reservation, ambiguity retention/late settlement,
shared-slot ownership after caller cancellation, document aggregation,
mock/live policy separation, overrun halt and request conflicts. The inline worker
checks logical orchestration; it does not establish real threaded timing. The
shared-ledger case uses separate ledger instances; actual multi-process/crash
contention remains a required later runtime check.

Consumer fixture contract: SDK fake `system_one(**request)` returns a mapping
with exact model, answers and nullable usage; HTTP fake `post(body, *, timeout)`
returns a Chat response envelope. Injected clients are always mock-origin and
require a ledger constructed with `origin="mock"`. Mock results have no live model
or actual charge. F2 labels are bool/null or registered Choice ID under
`raw_classifications`, with `needs_review`, empty probability `answers` and explicit
generated-label semantics. No downstream code should infer calibration from them.
