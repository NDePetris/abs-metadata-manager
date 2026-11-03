# Candidate processing and unfinished-book navigation

## Findings and limits

At starting head `2c7a453`, the authenticated GET and JSON/array validation
preceded `AbsSource.search`, `candidate`, candidate digest generation, JSON
serialization, SQLite lookup persistence and `verification_report` matching.
`run_verification` collapsed downstream ValueError/TypeError/KeyError into the
same `invalid_schema` diagnostic used for transport schema errors. The reported
incident lacks the `direct_get` marker attached to direct transport errors; this
supports a downstream failure but does not identify its exact field.

Two actual provider contract incompatibilities are established:

- `candidate` iterated `series: null`, raising TypeError after a valid response.
- ABS Audible series entries use `{series: string, sequence: string}`, whereas
  the parser understood `{name: string, sequence: ...}` and silently omitted
  those entries. This lost evidence rather than raising the reported exception.

Primary evidence: [ABS Audible.cleanResult](https://github.com/advplyr/audiobookshelf/blob/master/server/providers/Audible.js),
retrieved during this change, content blob `cf1a15ffbaf585cd759543fd2be710a90ef1c849`.
It explicitly emits null series for standalone books, display-string author and
narrator credits, nullable subtitle and language, and numeric minute duration.
[BookFinder](https://github.com/advplyr/audiobookshelf/blob/master/server/finders/BookFinder.js)
passes these Audible results through (retrieved blob `90fed951f65cf15b3b6d9af9940372ce27ee664b`).
Tests use synthetic values following those source shapes, not private recordings.
The incident payload and deployed ABS version were not inspected; **the precise
production trigger is still unknown**. A nullable series is a confirmed compatible
failure path, not a proven attribution of this incident.

## Processing policy

HTTP JSON errors retain `malformed_response` with stage `http_json`; invalid
top-level arrays retain `invalid_schema` with stage `response_schema`. Individual
nonobjects get `unsupported_candidate_shape`. Field validation, identity and
serialization failures get `candidate_normalization`, `candidate_identity` and
`candidate_persistence`, respectively. Candidate diagnostics include provider,
zero-based index, stage, fixed field path where known, expected type and actual
type. Unexpected exceptions use an unknown field, never arbitrary exception text.
The workflow also labels whole-lookup JSON serialization failures separately.
A database outage cannot durably save a diagnostic until persistence is available.

Null/absent values are retained as unknown evidence, not inferred clears.
String credits are never split on commas; named contributor objects and arrays
are supported and original roles remain in raw evidence. Both series keys and
multiple decimal positions are supported. ABS minute duration is converted to
seconds only for numeric values. Unsupported text/identifier/URL/duration shapes
are reported rather than coerced. Raw provider records remain intact locally.

One bad candidate does not erase valid candidates. Such a lookup is `partial`,
matches use only its valid candidates, and a visible warning discloses rejected
candidate count with expandable structural diagnostics. All-invalid results are
`unavailable`, never `no_match`. Partial/unavailable searches are not reusable
success-cache entries. Completed tasks and reruns never automatically retry.
Previous usable partial evidence can survive a later failed lookup with the
existing historical-evidence mechanism.

The ASIN/provider-ID/recording/fallback identity priority is unchanged. The
fallback digest retains the previous series projection for ABS's `series` alias,
so restoring that evidence does not invalidate earlier rejection identities.
Existing captures and human decisions are not rewritten.

## Review queue

Needs review is the initial filter. Accepted and All books are available beside
the book selector. Membership and counts come from the current durable
`verification_report`, which uses `review_fields` and valid `field_decisions`.
Human confirmation of a correction counts as review acceptance only while the
corresponding accepted target remains valid and active; an arbitrary
correction-needed decision is insufficient. Write approval/application is separate.

Previous/Next use the filtered title/item order. Confirm & Next in Needs review
advances to the next unfinished book, wrapping if necessary. Partial confirmation
does not finish a book. Once every applicable field is accepted, the book leaves
that queue. Finishing the final book shows completion and Show all books, without
jumping to an accepted book. Accepted can also have an explicit empty state.
Navigation never changes decisions; per-book unsaved drafts remain separate from
widget state. Existing identity and source rules govern import carryover and
library isolation. A browser restart reconstructs progress from SQLite.

## Safe real-library acceptance

1. Back up the local database with the app idle, then run the updated branch.
   Connect to the intended server/library. Do not apply or roll back metadata as
   part of this acceptance check.
2. Explicitly search the formerly failing book. Confirm a result list or a
   visible partial/failure message. Inspect Safe search diagnostic if needed.
   Share only that structural diagnostic, never Advanced raw evidence or tokens.
3. Alternatively, run `.\.venv\Scripts\python.exe scripts\diagnose_metadata_search.py`
   from the repository with the app idle. Enter the same title/ASIN and author;
   the key prompt is hidden. This performs one authenticated read-only GET under
   the workspace lock, normalizes in memory, prints counts/structural diagnostics,
   and does not persist results. `--with-cli` is unnecessary for this parser check.
4. Compare Needs review/Accepted counts with a few known complete and partial
   books. A reference selection or search alone must not finish a book. Confirm
   only values actually reviewed; confirmed corrections should remain staged in
   Changes with no remote writes. Try Next and Confirm & Next, then empty queues.
5. Edit a draft, switch filters/books and return. Restart the browser to verify
   durable progress (unsaved drafts are session-local). Import a fresh snapshot
   only through the normal explicit Import action; retained acceptance still
   requires the existing identity/value validity rules.

## Validation

Synthetic Streamlit interactions cover partial results with unsaved drafts, queue
changes, empty filters, pending corrections, partial confirmation, Review Later,
reopening, browser-session reconstruction and import carryover. Existing tests
exercise cross-library isolation, rejected candidates and guarded writes.
Desktop screenshots at [1024×800](review-queue-1024.jpg) and
[1280×800](review-queue-1280.jpg) show the native comparison grid and inline filter.
No production metadata, media, paths, folders or journal records were changed.

Final full-suite recheck: **322 passed, 3 skipped** across three isolated groups
(94 passed / 3 skipped, 88 passed, 140 passed). JUnit results verify all 19 test
files and 325 unique cases were included exactly once, with no failures/errors.
Official-binary tests skip before opening any existing personal CLI config;
fake-subprocess and local-HTTP tests pass. The stale pre-GET search test now
mocks the authenticated HTTP session and asserts that search never uses CLI.

Ruff, dependency consistency, Windows `start.ps1 -Check`, native UI detector and
working/staged whitespace checks pass. An additional comparison against the
starting commit preserves 11 supported candidate identities and reproduces the
old nullable-series TypeError. A fake-adapter invocation of the operator
diagnostic confirms structural output without candidate values/credentials or
database persistence. The earlier focused Streamlit/contract run passed 63 tests;
the final full recheck includes all subsequently added source-backed/queue cases.
