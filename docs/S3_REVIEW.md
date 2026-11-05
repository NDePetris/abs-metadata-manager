# S3 exception-first review and completeness

S3 implements issues #10–#13 on `codex/s3-exception-review`, based on main's
merge of S2 PR #19 (`4150943`). It does not implement S4 or touch the production
baseline, credentials, media, or unresolved production operation 25.

## Changes by issue

- **#10:** Match & Verify counts Pass / Needs attention / Needs evidence separately
  from historical manual acceptance. The default queue has one entry per book,
  ordered by disposition and essential defects. Quality, affected field, reason,
  title and author filters retain access to passing books. All books uses stable
  title ordering. Previous/Next and snapshot-scoped drafts retain the compact
  Current / Reference / Desired editor. Confirm & Next follows quality exceptions;
  accepting editable fields cannot hide pending corrections or uninspected covers.
  Each editable field shows quality basis and source; expanded reasons include
  the existing evidence/capture provenance. Essential bibliographic progress stays
  visible even when artwork or hygiene prevents a full-book Pass.
- **#11:** Explicit selection retrieves at most 12 saved ABS images. Each retrieval
  checks live item/cover identity, the existing 10 MB limit, supported image decoding
  and a 25-million-pixel limit. Failures are persisted separately from a saved path
  and from uninspected artwork. Images remain in bounded browser-session state;
  only state, identity, timestamp and SHA-256 persist in existing annotations.
  Individual inspection checkboxes bind to the content hash. One submit confirms
  only checked covers through existing field-decision history. Re-retrieving changed
  bytes or failed retrieval invalidates quality; changed item/path/version reopens
  review. Any attractive, appropriate edition artwork for the correct work is allowed.
  Ebook decisions describe ABS displayed artwork, not authoritative Calibre artwork.
- **#12:** ABS `media.metadata.genres` and `media.tags` are inventoried separately.
  Exact values, counts, duplicates, whitespace/case variants and all affected books
  are visible in native dataframes. Unsupported shapes fail explicitly rather than
  being interpreted as categories. One minimal preference table stores exact-value
  Acceptable / Unwanted / Unreviewed preferences per server, library and category
  kind. Preferences survive imports, never verify bibliographic facts, never silence
  explicit individual review-later decisions, and never rename/delete anything.
  Missing optional categories pass. Duplicate/case/unwanted values remain findings
  until changed in the native application and observed in an import.
- **#13:** Report inputs are batched per invocation: books, evidence attachments,
  annotations, decision history, corrections and staged findings. Existing compatibility
  checks and expected-transition fallbacks remain intact. Candidate history is
  interpreted once per item. Duplicate title counts use a single Counter instead of
  rescanning the inventory for every label. No report cache, ORM or indexer was added.

## Why fields previously appeared to need confirmation

S2 already assigns automatic Pass to sufficiently corroborated fields. The old
selector and field captions emphasized manual acceptance, so that behavior was
largely invisible. Synthetic tests now demonstrate visible independent Pass with
no field decision, as well as human-confirmed Pass with a different explanation.

Evidence gaps are real: work-only matches cannot establish recording narrators,
partial/contradictory evidence remains unresolved, and a nonempty cover path proves
neither retrieval nor appropriateness. No broad S2 classification relaxation was
justified. Narrow policy extensions consume cover retrieval and category hygiene
evidence through the existing `quality_policy`; there is no second assessment engine.
No conclusions about actual provider coverage in the real library are claimed.

Finalization corrects an embedded-title false positive reported for Edgedancer:
`Stormlight Archive 02.5 - Edgedancer` in captured audio tags no longer makes the
clean saved `Edgedancer` title a conflict. Audit retains the discrepancy as an
informational embedded-source observation; verification names the disagreeing
embedded fields and keeps their cleanup deferred. Folder agreement alone still
means Needs evidence, missing audiobook narrators still need attention, and credible
external contradictions, human decisions and outstanding corrections retain their
existing treatment. This change neither stages title corrections nor edits files.
Six synthetic regressions cover two harmless prefixes, independent corroboration,
a credible external title contradiction, durable decisions/pending corrections and
the displayed explanation. Finalization validation: **382 passed, 4 skipped in
104.52 seconds**; Ruff, dependency consistency, launcher and whitespace checks pass.
The skips remain the three protected personal-CLI-profile cases and private export.

## Measured performance

Windows / Python 3.13.14, isolated temporary SQLite databases, 100 synthetic
audiobooks plus 650 synthetic ebooks. Wall times are observations, not thresholds.
Snapshot creation uses the normal durable per-book import path and is measured
separately. SELECT counts instrument `Store.rows`; connection PRAGMAs and transaction
statements are excluded. The baseline inventory has no captured evidence or decisions;
the richer demonstration below tests matching, decisions and pending corrections.

| Measurement | S2 implementation | S3 implementation |
| --- | ---: | ---: |
| Snapshot setup | 9.990 s | 11.173 s |
| Report computation | 11.830 s | 0.321 s |
| Report SELECTs | 12,753 | 13 |
| Queue/duplicate-label processing | 0.126 s | 0.005 s |

The first queue timing accidentally included the Streamlit import; it is excluded
from this table. The old title-label loop was measured separately with
`--legacy-queue`, after imports. No snapshot setup improvement is claimed.
The report improvement addresses measured repeated reads, not evidence acquisition
speed. Changed-identity historical decisions still use conservative individual
fallback checks; large histories and many such transitions need further measurement.

`scripts/profile_review.py --ui` additionally measures full native Streamlit first
render and rerender against a 750-book categorized library. Native dataframes keep
the exact affected-book list searchable without hundreds of individual paragraphs.
The final categorized run measured setup 11.555 s, report 0.591 s / 13 SELECTs,
queue 0.007 s, first render 2.250 s and rerender 0.759 s. An earlier paragraph-list
iteration measured first render 2.075 s / rerender 0.792 s; the small UI difference
is within run-to-run variation, so no speed improvement is attributed to dataframes.
These AppTest wall times include report/UI serialization, not browser painting or
network latency. There is no measured S2 full-rerender baseline and no claim of a
full-browser speedup. Reproduce with the commands below.

## Reduction in manual work

`scripts/demonstrate_s3.py` creates 100 audio / 650 ebooks, including strong and
partial evidence, 10 ambiguous audiobook editions, missing essential values,
50 missing covers, valid images, common genres, unwanted/case-inconsistent tags,
10 pending corrections, 600 scoped Calibre matches and 50 unmatched ebooks.
It uses fake adapters/providers and an imported synthetic catalog throughout.

| Result after three category preferences and one inspected gallery page | Count |
| --- | ---: |
| Fields passing from independent evidence or Calibre authority | 2,730 |
| Of those, editable comparison fields | 2,050 |
| Books needing attention | 70 |
| Books needing additional evidence | 668 |
| Fully passing books after explicit artwork inspection | 12 |
| Old workflow's editable-field confirmation workload | 3,750 |
| Remaining unresolved editable field checks | 210, across 90 books |
| Category-field checks resolved by library preferences | 1,480 |
| Covers individually inspected on one gallery page | 12 |
| Remaining cover cases | 738, including 50 missing covers |
| Remote writes | 0 |

The 2,730 figure excludes optional-absence and category-preference passes. Calibre
authority is counted but stays labeled separately from independent external evidence.
The 210 open field checks include staged corrections; staging never counts as an
observed fix. There are 738 books still requiring attention/evidence of some kind,
not 210 total user actions. Cover checks overlap bibliographic cases; 20 books also
have native category remediation. Acquisition can be batched and several fields can
be confirmed in one submit, so these are review units, not predicted mouse clicks.
The gallery reduces navigation and confirmation submissions, never the number of
artworks that must actually be inspected. No target automatic Pass percentage was used.

## Validation and limits

The full synthetic suite includes Streamlit interaction, draft/navigation, source
switching, candidate rejection, Calibre authority, journal/write/recovery regressions,
independent versus human Pass, cover absence/retrieval/content changes, exact category
scope, optional absence and no requests/writes on rerender. Required local checks:
pytest, Ruff, pip check, Windows launcher and git diff whitespace validation.
Final local result: **376 passed, 4 skipped in 99.73 seconds**. Ruff, `pip check`,
`start.ps1 -Check` and `git diff --check` passed. GitHub Actions results are recorded
on the delivery PR separately; local results do not stand in for CI.

Four expected local skips: three official CLI protocol tests require a clean OS
profile (personal CLI configuration was not opened); the private historical-export
test is excluded. Synthetic fixture creation initially failed inside the sandbox's
temporary directory; completed runs used normal temporary/loopback access with
test isolation and the external-network guard intact.

Desktop browser checks cover 1024×800 and 1280×800: exception queue, long-title
comparison, deliberate multi-cover retrieval and the category impact view. Synthetic
placeholder covers are intentional; screenshots do not demonstrate real artwork.
The comparison and gallery have no horizontal document overflow at either size.
Saved visual evidence: [1024 queue](screenshots/s3-queue-1024.jpg),
[1280 queue](screenshots/s3-queue-1280.jpg), [cover gallery](screenshots/s3-gallery-1024.jpg)
and [category impact](screenshots/s3-hygiene-1280.jpg).

Same-path image changes that expose no changed cover version cannot be detected
without explicit retrieval. The UI instructs operators to refresh Import and retrieve
again after native edits. No background checking was added. Image decoding uses
Pillow already supplied by Streamlit; there is no new dependency or image framework.
There is no installed-Calibre, real provider or production CLI end-to-end claim.

S4 can consume the unchanged dispositions/reasons/provenance, durable field decisions,
cover observation identity/hash/state, library category preferences and existing
correction/journal states. Native correction instructions are available now; external
change reconciliation, cover writers and genre/tag writers remain out of scope.

## Reproduction

```powershell
.venv/Scripts/python.exe -m pytest -ra
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pip check
./start.ps1 -Check
.venv/Scripts/python.exe scripts/profile_review.py
.venv/Scripts/python.exe scripts/profile_review.py --legacy-queue
.venv/Scripts/python.exe scripts/profile_review.py --ui
.venv/Scripts/python.exe scripts/demonstrate_s3.py
.venv/Scripts/python.exe -m streamlit run scripts/visual_fixture.py
```

These scripts create their own synthetic fixtures and do not open the real baseline.
