# Metadata quality and independent verification

## Startup and validation

From PowerShell in `G:\Projects\abs-metadata-manager`:

```powershell
.\start.ps1 -Check
.\start.ps1
```

For a fresh checkout, use `.\start.ps1 -Install -InstallCli` once. The launcher binds to `127.0.0.1:8501`. In Settings, use the existing Cloudflare HTTPS server URL and API key. No direct LAN connectivity is required. Credentials remain in session/environment/ignored configuration, never the verification tables. To try the UI without credentials, load the synthetic sample in Import, then select Match & Verify.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests
```

Tests use synthetic providers, stored public fixtures, fake adapters, and a loopback HTTP simulator. They never query third-party services or production. Install the official CLI to enable its protocol smoke tests; the private historical export regression is optional outside this workspace. Windows tests need normal temporary-directory and loopback permissions.

## Status meanings

| Status | Meaning |
| --- | --- |
| Verified | A uniquely matched external candidate supports the exact field value; provenance identifies the candidate and source. Recording credits additionally require recording identity. |
| Consistent | Values agree with local evidence, a read-only authoritative Calibre catalog, or a human-selected edition. This does not assert independent external verification. |
| Conflict | Values or local structural checks disagree. Inspect the evidence; this does not automatically establish that ABS is wrong. |
| Missing | A required saved value is absent, no saved cover/chapters exist, or an assigned series has no valid position. An intentionally unnumbered edition may need review. |
| Unverified | Evidence is unavailable, insufficient, or ambiguous. Optional blank subtitles, series, translators, and identifiers are not automatically errors. |
| Not applicable | Ordinary ebooks have no narrator/audio chapter/tag requirement; standalone books need no series position. |

Human field decisions have their own evidence, source and history. Confirmed correct, needs correction, unable to verify, and review later are separate from independent external status and from write approval. Generic historical review annotations remain available but are not converted into accepted fields. A decision can be reopened deliberately. A new external disagreement remains visible and does not silently revoke acceptance.

An accepted field survives a new completed import only while its server, library, current item identity, relevant edition evidence and value remain compatible. Unrelated metadata changes do not invalidate the field. Changed values or identity evidence reopen review; a changed ID, moved path, or ambiguous replacement is handled conservatively and cannot silently inherit acceptance. Removed items retain history. Rejected candidate identities and their evidence are retained across compatible imports. New imports always expire prior write approvals.

## Sources and limitations

### Audiobookshelf provider searches

`abs-cli metadata providers` reads `GET /api/search/providers`. Only returned book provider IDs appear in the new-run UI; Audible/Audnexus IDs sort first. Discovery is explicit, not part of rendering or startup. `abs-cli metadata search --provider ID --title TITLE_OR_ASIN [--author AUTHOR]` reads `GET /api/search/books`. Search uses the same Cloudflare connection as auditing. No Match, Quick Match, scan, replace, cover mutation, or chapter mutation is involved.

The installed 1.2.0 executable's full help was inspected. It supports title and author flags, with ASIN accepted in the title argument for Audible providers. It has no separate ISBN, series, language, edition, or duration query flags. All captured identifiers and recording details are nevertheless used in candidate comparison. ABS search returns a raw candidate array, not a `matches` wrapper. ABS provider durations are minutes and are normalized to seconds before comparison with saved media duration. Original responses are retained unchanged.

Provider availability, regional catalogs, returned fields, and attribution depend on your server configuration. The app discovers IDs at runtime; live availability has not been established in this development workspace because no configured server credentials were available. A source URL from the candidate is preserved; when absent, the corresponding ABS search URL identifies the captured search origin. Built-in bibliographic providers and custom providers are conservatively treated as work-level unless a supported audiobook provider establishes recording identity. ABS may itself return an empty array when an upstream service fails; the app cannot distinguish a server-hidden failure from a genuine empty result.

Protocol evidence: [CLI metadata command](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Commands/MetadataCommand.cs), [CLI metadata service](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Services/MetadataService.cs), [ABS search controller](https://github.com/advplyr/audiobookshelf/blob/v2.37.0/server/controllers/SearchController.js), and [ABS BookFinder](https://github.com/advplyr/audiobookshelf/blob/v2.37.0/server/finders/BookFinder.js).

### Open Library

The documented Search API at `https://openlibrary.org/search.json` is used only for targeted human-initiated one-book searches. Library-wide queues are refused. Results are cached and reuse needs no request. Requests are sequential with at least 1.5 seconds between starts, an identifying User-Agent, bounded timeouts, and bounded transient retries. Set optional `METADATA_CONTACT` in `.env` to include contact information in the User-Agent. Only small, selected fields are requested.

Work-level results corroborate titles/authors. Search results aggregate editions; the app does not select an arbitrary ISBN, narrator, or cover from that aggregation. No audiobook narrator or recording verification is claimed. Source links point to Open Library work pages. Follow [Open Library API usage guidance](https://openlibrary.org/developers/api) and [Search API documentation](https://openlibrary.org/dev/docs/api/search); use bulk datasets rather than converting this targeted lookup into a bulk backend.

### Google Books

Optional `GOOGLE_BOOKS_API_KEY` configures public `GET https://www.googleapis.com/books/v1/volumes` requests. Keys are passed only to Google, excluded from stored query provenance, and redacted by withholding request exception text. Authentication, API availability, and quotas follow [Google Books API documentation](https://developers.google.com/books/docs/v1/using). No OAuth, payment account, or AI account is required by the application. This source can be disabled by leaving the key blank.

Queries use ISBN where present, otherwise title/author. Volume identifiers, language, publisher, release details, and returned bibliographic metadata are captured. These are bibliographic editions, not proven audiobook recordings. The app never uses Google Books to independently verify recording narrators or a saved cover image.

### AudioSilo investigation

[AudioSilo Meta's documented API](https://meta.audiosilo.app/docs/api/) is read-only, public, recording-aware, and also exposes an Audiobookshelf provider route. A separate direct client is deferred: its work/person/recording relationships need their own normalization and fixtures before claiming recording verification. If configured as an ABS custom provider, its discovered ID can be searched and evidence inspected through the existing adapter, conservatively at work level. This does not block the supported ABS audiobook providers.

## Matching and provenance

Each candidate is retained with original JSON, source, source URL, normalized values, query, and capture time. Matching records all positive signals and contradictions. It considers ASIN, checksum-valid ISBN (including ISBN-10/13 equivalence), title with explicit series/Unabridged labels normalized, full contributor sets, language aliases, series name/decimal position, narrators, duration, abridgment, publisher, and release details when supplied.

Title alone never establishes recording identity. A work match requires title plus authors, or an exact identifier, without contradictory identity fields. A recording match additionally needs an audiobook provider and either a valid exact ASIN or matching narrators plus duration. ISBN agreement alone never proves recording identity. Duration tolerance is the larger of one minute or 2%. Contradictory duration, language, identity, or abridgment prevents an independent recording match. No numerical score authorizes any update.

Candidates show one of five assessments: correct work and recording; correct work with recording uncertain; different recording of the same work; wrong work; or no match found. Use **This is my edition**, **Not my edition**, **Wrong book**, or **Unsure / review later** with evidence. Rejected candidates are hidden from the normal candidate selector; enable **Show rejected candidates** to inspect or reconsider them. Refine title/ASIN and author searches, and filter returned candidates by narrator. If your edition is absent, save a manual edition reference and recording evidence; it remains a human association. A manually selected same-work recording may supply a pending correction even when its credits conflict with saved values, but it never gains independent verification through that selection.

Multiple plausible candidates remain ambiguous/unverified until inspected. Explicit edition decisions retain their evidence; a human association is not itself independent external verification. A work-level match cannot independently verify narrators, recording-specific series positions, or cover correctness. The algorithm deliberately prefers leaving fields unverified over choosing an edition by result order.

## Local evidence

Every completed snapshot book receives 11 field assessments: title, subtitle, authors, narrators, translators, series name, series position, identifiers, cover, chapters, and embedded consistency. Original identity/JSON remain separate from normalized values. Review proposals continue to use the existing five writable fields.

Folder titles are evidence, not authority. Ebook comparisons strip trailing Calibre numeric IDs for display, suppress audiobook narrator requirements, and avoid inferring series hierarchy or title errors from sanitized/truncated Calibre folder names. Legitimate subtitles survive; duplicated titles or explicit promotional book labels are flagged for inspection. Overlapping audit rules cannot create duplicate actionable proposals for the same field.

Embedded M4B title, album, artist, and composer tags are shown only when already exposed by ABS; no ffprobe/file access occurs. Composer is never silently equated with narrator. Stale tags can reappear after reimport even when ABS saved metadata is correct. Saved contributor arrays and edition-specific recording credits are preserved.

Chapters distinguish missing chapter data, a count without timings, generic labels, and captured named chapters. Captured timestamps are checked for finite numeric values, negatives, order, overlap, end before start, and duration boundaries when available. This validates structure, not spoken content or the edition's true chapter names. Cover presence is reported separately from correctness. A path or externally returned cover URL never proves that the saved image is correct; use the explicit read-only preview and record visual review evidence. Cover preview bytes are not retained in the offline cache.

## Runs, cache, cancellation, and offline operation

Runs bind to an audit snapshot, source, and server scope. Queries and tasks are saved before network requests. A sequential action holds the same OS workspace lock as audits and writes. Every response is saved synchronously before proceeding. Successful and empty results are cached by source, server, normalized query, and schema version; identical queries within a run also reuse evidence. Cache reuse never discards the original capture time. Refresh explicitly creates new evidence. Latest lookup outcomes remain available with their raw source records. A failed refresh retains the previous successful response for the identical query as dated cached evidence while displaying the latest failure.

Preparation makes no network requests. Start/resume is an explicit form submission. Set the action limit to the collection size (e.g. 110) to process all audiobooks, or keep smaller batches for easier pauses. Progress shows saved/total counts. Cancel signals the current run and stops pending queries at the next book boundary; it cannot recall an already submitted read. Streamlit Stop/process interruption retains completed tasks. Resume skips attached tasks; use the explicit retry checkbox for unavailable records. An unavailable response pauses further library requests. Empty and unavailable outcomes are distinct; conflicting candidates are shown through their matching contradictions. Public requests respect `Retry-After`; long delays stop the action for an explicit later retry.

Rendering, filters, exports, and ordinary reruns use stored evidence only. There are no workers, startup requests, scheduled verification, or LLM calls. Offline report inspection, filtering, manual reviews, Calibre comparisons, and exports continue to work. A protocol-only `EvidenceAdjudicator` extension point exists for future explanation/adjudication; nothing invokes or configures an AI service.

## Read-only Calibre catalog

Run this on the machine with your Calibre library, substituting its actual library path:

```powershell
calibredb list --with-library 'D:\Calibre Library' --for-machine --fields title,authors,series,series_index,identifiers |
    Set-Content -Encoding utf8 calibre-catalog.json
```

The JSON includes numeric record IDs automatically. Upload it through the Calibre catalog form. Export files contain private inventory data: keep them outside Git or under ignored `data/`. See [Calibre list documentation](https://manual.calibre-ebook.com/generated/en/calibredb.html#list).

The importer accepts a single-library JSON array. It matches ebook records through a folder's numeric Calibre ID, valid ISBN, or exact title plus all authors. Ambiguous matches remain unmatched. It records catalog digest, import date, record ID, and values; Calibre comparisons are consistent/conflicting, never mislabeled as independent online verification. No Calibre database, EPUB, or managed folder is opened or edited. Custom translator/subtitle/cover columns are not currently mapped. Without a uniquely matched catalog record, the report displays its ABS/filesystem-only limitation.

## Pending corrections and safety

A supported discrepancy may be sent to the correction workflow only when the candidate establishes a recording match. Ambiguous recordings also require a recorded human edition selection. A new proposal includes source evidence and starts **pending**. Existing proposals for that book/field must be edited or rejected first. Separate field approval and the exact apply preview digest, source identity checks, durable journal, unrelated metadata guards, and reconciliation requirements are unchanged.

Ebooks remain read-only. Record the proposed Calibre correction and supporting evidence, then edit it in Calibre yourself. Marking an edit as made is a reminder, not verification: import a later Calibre catalog and check that its uniquely matched record contains the proposed value. A later ABS import provides a separate observation of synchronization. Translator, identifier, cover, chapter, and embedded-tag remediation is external/manual. Verification never invokes the remote writer, and there is no automatic correction authority. No production metadata writes were performed during development.

## Actual coverage and remaining gaps

The ignored historical 110-book fixture produced a complete report with **110 books × 11 fields = 1,210 assessments**, embedded evidence for 110, saved cover paths for 110, and captured chapter arrays for 106. It produced **zero externally verified fields** before external lookups. These counts describe captured historical evidence, not current production metadata accuracy. No private inventory was added to Git.

The full 656-ebook production catalog and live provider catalogs were not available for validation. Synthetic ebook, Calibre, missing-credit, multi-author/narrator, edition, and failure fixtures cover those paths. Image correctness, spoken chapter names, unknown translator credits, missing embedded tags, and unavailable provider fields remain unverified unless supported by recorded evidence. The report and source-coverage table expose those gaps directly.
