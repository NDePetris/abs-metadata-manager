# S2 quality and evidence engine

`Store.verification_report(audit_id)` assesses every book in a completed snapshot,
including zero-finding books. It retains detailed statuses, provenance, matches,
candidate rejections and durable human decisions. Each field now includes
`disposition`, `reason_code` and `quality_applicable`; each book includes
`disposition` and `quality_counts`. Priority is Needs attention, then Needs
evidence, then Pass. `verification.quality_policy` is pure and does not alter its
inputs, stage corrections, grant approval or fetch evidence. Existing
`review_status` continues to describe manual acceptance, independently of quality.

Pass bases are `independently_verified`, `human_confirmed`, `calibre_authority`,
`optional_absent` and `not_applicable`. Deferred chapter/file inspections have
`inspection_deferred` and `quality_applicable=False`; their diagnostic statuses
are preserved and do not create automatic outstanding file work. Captured invalid
chapter timestamps remain a visible structural defect. Present genres
and tags remain Needs evidence until reviewed; absent ones are optional. Covers
remain Needs evidence even with strong bibliographic evidence: a cover path is
not proof of artwork correctness. A full book can pass after explicit visual
human confirmation through the existing durable decision API; the cover review
interface belongs to S3. S2 cannot honestly claim an automatic full-book Pass
for uninspected artwork.

Required title, authors, audiobook narrators and cover cannot pass when missing.
Assigned series lacking a position need attention; an explicit compatible
`series_position` confirmation can document a legitimately unnumbered entry.
Unknown/later/reopened decisions stay unresolved. Pending corrections, including
Calibre-verified targets awaiting ABS observation, stay actionable. Compatible
current-value human confirmations survive imports using existing identity/value
checks. Later contradictory captured evidence reopens quality without changing
historical decisions or granting/revoking a write approval. A person can explicitly
confirm again after inspecting that conflict. Reference selection alone does not
provide independent verification.

The matcher separates work agreement from recording identity. Title and authors
can corroborate across several editions when their supplied values agree. Sole
results and narrator/duration agreement alone do not prove recording identity.
An exact valid ASIN, or an exact valid ISBN with narrator and duration agreement
from a recording-scoped candidate, can establish recording identity only without
identifier, credit, duration, language or abridgment contradictions. Multiple
ISBN-based recording candidates without a shared exact saved ASIN remain
unresolved. Work-only
sources cannot establish recording identity. ISBNs in aggregate work results
never establish a recording. This policy does not infer missing identifiers,
rank providers or count repeated upstream results as independent votes. Sources
that expose no suitable identifiers leave recording-specific fields unresolved.
Omitted source identifiers leave those saved identifiers unverified, rather than
asserting a conflict. Conflicting references retain all provenance and produce Needs evidence; a
credible discrepancy against one established reference needs attention. Explicit
human selection remains Consistent, independently of field confirmation.

`verification_workflow.evidence_coverage(report, source=None)` returns book counts
for attempted, ok, matched, no_match, partial, unavailable, unattempted, Calibre
comparisons and remaining, plus the latest capture timestamp. `ok` means successful
candidate acquisition; `matched` means at least work identity, not a recording or
quality pass. Remaining means unattempted audiobooks, partial/unavailable attempts,
and ebooks lacking a Calibre association. No-match is a completed attempt, not
verified quality. Source filtering applies to audiobook attempts; ebook counts
always describe local Calibre authority. Failed refreshes report unavailable
while preserving earlier successful compatible evidence and its original capture.
`run_coverage(store, run_id)` counts durable task states, new lookups, reused
lookups (including identical queries within a run), and safe failure summaries.

The existing verification task/checkpoint tables and cross-process operation lock
are reused. No schema, dependency, provider or scheduler is added. Cache keys now
include normalized saved values, identifiers, duration, language and abridgment;
legacy keys naturally miss once. Identical compatible queries share successful
lookups. Partial/unavailable results are not success-cache hits. Each invocation
is sequential and transport-paced, stops after the explicit limit or provider
outage, and leaves durable remaining tasks. No resume happens without a button.
The UI caps batches at 25 (default 10); the existing lower-level runner retains a
validated maximum of 110 requests per call. Open Library remains targeted-only,
one explicitly selected book. Ebooks are excluded from batch public searches and
use already imported Calibre comparisons. Retry resets only unavailable task
attachments; refresh explicitly ignores successful cache.

S3 now consumes report dispositions, per-field reasons/provenance and coverage
directly for its exception queue, explicit visual cover review and hygiene
discovery; see [S3 delivery](S3_REVIEW.md). No live baseline, production database, credentials, files or metadata
were used in S2 tests; production operation 25 remains unresolved and untouched.

## Validation

Final local Windows/Python 3.13.14 run: `python -m pytest -ra` with JUnit output,
**357 passed, 4 skipped in 94.10 seconds**. The synthetic 100-audiobook /
650-ebook exercise took 23.93 seconds including snapshot creation and report
checks. This is a regression exercise, not a production performance benchmark.
Ruff, `pip check`, `start.ps1 -Check` and `git diff --check` passed. Streamlit
AppTest covers explicit batch execution, cache reuse, refresh and no requests on
rerender; no new browser screenshot acceptance is claimed.

Three official CLI protocol tests skipped because the real OS profile contains
CLI configuration (not opened). The private historical-export test was excluded
by default. No live provider, installed-Calibre or production end-to-end
verification is claimed. Initial sandbox attempts failed during pytest temporary
directory setup; successful runs used normal temp/loopback access with the
repository's synthetic isolation and external-network guard intact.
