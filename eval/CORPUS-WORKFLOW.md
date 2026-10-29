# Lawful corpus and human annotation

This is a workflow contract and source tooling, not a selected corpus, rights
approval, staffed annotation team or custody seal. No external document was
downloaded, labelled or evaluated in construction. The authored demo remains
synthetic development material only.

1. A rights owner selects native-text PDFs and records URL, source version,
   retrieval time, original-byte hash, explicit permitted use/redistribution terms
   and versioned evidence. Public hosting alone leaves rights pending. The pure
   `rights_revision` function returns a proposed append-only revision. Moving to
   verified requires a supplied `RightsAuthority` that authenticates the real
   actor and evidence. Excluded documents stay in audit history. A changed PDF
   gets a new document identity. The storage owner must CAS the expected revision
   atomically; these pure functions do not persist a decision or grant rights.
2. `validate_pack` checks a frozen declared rights snapshot and rejects pending,
   excluded, changed-use and duplicate records. A real host must also verify the
   ledger's current head and evidence signatures, match each PDF byte hash, and
   enforce later rights revocation; a self-consistent JSON hash is not permission.
3. An independent family custodian groups issuers, templates, amendments and
   near duplicates before tuning. `near_duplicate_report` supplies bounded
   NFKC/casefold token-Jaccard screening and transitive related-document groups.
   Shared issuer/template/raw hash/family connects a group even without token
   similarity. A connected group spanning splits or declared families blocks the
   proposed mapping. Human screening remains mandatory for small paraphrases,
   boilerplate, related issuers and low-overlap amendments; no algorithm certifies
   unrelatedness. An empty/unknown issuer or template must be researched, not used
   to split similar documents. Document and family IDs must not encode labels.
4. Before tuning, freeze the full split and the selected prospective floors. The
   selected full target is 12 documents/6 unrelated families, 6 development docs/3
   families and 6 held-out docs/3 unseen families, 180 atoms/60 mandatory overall,
   and held-out 90 atoms/30 mandatory/20 absent/10 conflict/20 conditional-admin.
   Preliminary evidence is explicitly smaller: held-out 4 docs/3 unseen families,
   60 atoms/20 mandatory/10 absent/5 conflict with conditional/admin counts
   separately reported. These are unmeasured targets. The existing public
   `dataset_eligibility` helper reports count gaps and never authenticates custody
   or grants acceptance. Do not pad counts with duplicate documents or ancestors.
5. Human annotators label original-string intervals, atomic parent/child lineage,
   independent modality/administrative/conditional flags, applicability and literal
   supporting/contradictory evidence IDs. A second distinct human reviews all
   mandatory/conflicting held-out labels. Disagreements preserve both label
   digests, dimension, rationale and adjudicated resolution digest. Missing second
   review or unresolved disputes prevent declared adjudication completeness.
   `validate_annotations` checks closed shape, literal anchors, hash consistency,
   reciprocal acyclic ancestry and registered evidence references. It does not
   decide semantic equivalence or certify expertise/independence. The actual
   page/evidence mappings must match source_pages_sha/evidence_sha canonical
   commitments; the isolated host must additionally bind those mappings to the
   actual supplied artifact/source digests and frozen vendor corpus. Stable page
   IDs are distinct from display numbers.
6. Keep private held-out labels, label paths, disagreements and private predictions
   exclusively in the externally operated custodian environment. Implementers
   receive development labels only. This repository contains schemas, no private
   gold or CUSTODY file. Do not execute held-out annotation functions in an author
   workspace merely because their Python signature accepts a mapping.
7. The custodian seals label/split/scorer/source/parser/normalizer/policy/model/SDK/
   price/budget hashes before any comparison. The [runner](RUNNER.md) requests
   authorization and atomic spent-set consumption before method dispatch. Failure
   diagnosis exposes only policy-approved aggregates; any disclosed held-out
   cases become spent development material. New confirmation needs new unseen
   families. No caller boolean or fake port establishes independent custody.

`corpus.schema.json` documents example closed wire shapes. Runtime Python checks
add reciprocal lineage, literal span, status/evidence and hash/CAS constraints that
JSON Schema alone cannot establish. No JSON Schema validator was installed or run.
The annotation summary counts atomic leaves only and reports non-leaf structural
parents separately so parent/child records cannot inflate the gold denominator.
Custodians must preserve that distinction in scoring packets. No scores, labels or
human roles are manufactured by these tools.
