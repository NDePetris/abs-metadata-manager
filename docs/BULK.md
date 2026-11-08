# Bulk workflow

## Windows launch

Install Python 3.12+ and Node.js 20.19+ (or 22.12+). In this checkout:

```powershell
.\start.ps1 -Install -InstallCli
.\start.ps1 -Check
.\start.ps1
```

Open http://127.0.0.1:8501. The frontend is bundled into `frontend/dist` and served
by FastAPI; normal launch needs no Node server. `-Port 8502` changes the port.
`-Legacy` launches the preserved Streamlit app. The CLI must pass the existing
1.2.0 capability and API-key/no-refresh-token checks before writes are enabled.
An existing CLI can be selected through `ABS_CLI_PATH`.

## Export → external AI → review → apply

1. Connect with your ABS server URL and API key. Credentials remain in backend
   memory; they are not stored in the browser or exports. Select the correct library.
2. Export JSON. Wait for the complete download. Membership is checked before and
   after expanded item reads. Keep the downloaded file and `data/bulk` snapshots.
3. Give the export and schema below to your external AI tool. Suggested instruction:
   “Return corrections JSON only, following corrections.schema.json. Copy source
   identifiers and before values exactly. Propose only supported fields. Preserve
   edition narrators, contributor roles and multiple series. Explain changes with
   reason. Omit uncertain changes. Do not invent missing information.”
4. Import the resulting JSON in the original library. Review old/new values and
   reasons. Conflicting before values cannot be selected. Filter, select eligible
   fields, exclude individual fields, and edit JSON values as needed. Nothing is
   preselected. Save edited proposals if you want to continue later.
5. Review audiobook update. Inspect the exact selected values and library. Type
   the displayed `APPLY …` phrase and click **Apply confirmed changes** once.
6. Observe per-book results. Verified means a fresh ABS read matched approved
   metadata and protected state. Ebooks instead download a Calibre correction
   report for manual review/update; no Calibre connection or synchronization is claimed.

Selection applies to individual fields. Array targets replace the complete list.
Omitted fields stay unchanged. Description previews scroll; expand Edit value to
inspect the full JSON text. Imported markup is displayed as text, never executed.

## JSON contract

Export version 1 contains `schema_version`, `snapshot_id`, `scope`, `library_id`,
`book_count`, and `books`. Each book has `item_id`, an opaque `identity` fingerprint,
`kind`, `current`, `context`, `cover_present`, and chapter title/start/end summaries.
Current includes all eight supported fields. Context includes available publisher,
language, ISBN, ASIN, publication date, edition and abridgement. Null means ABS did
not supply a value. No credentials, private paths, playback progress, or raw
operational data are exported. Treat metadata and downloaded files as private.

Corrections must follow [corrections.schema.json](corrections.schema.json):

```json
{
  "schema_version": 1,
  "snapshot_id": "COPY_FROM_EXPORT",
  "scope": "COPY_FROM_EXPORT",
  "items": [{
    "item_id": "COPY_CURRENT_ABS_ITEM_ID",
    "identity": "COPY_FROM_THIS_BOOK",
    "reason": "Remove duplicated series wording from title",
    "changes": {
      "title": {"before": "Exact original title", "after": "Correct title"}
    }
  }]
}
```

The placeholders above are instructional; copy real identifiers from your export.
The original private snapshot must still exist. Version, scope and current item
identity must match. No legacy-ID resolution or legacy proposal conversion occurs.
Unknown properties, duplicate JSON keys/items, nonfinite numbers and unsupported
fields are rejected. Upload size is limited to 25 MB. The server validates all
edits again before preview/application.

Supported targets:

| Field | Target |
| --- | --- |
| title | Nonblank string |
| subtitle | String; empty string explicitly clears it |
| authors, narrators | Arrays of unique nonblank names |
| series | Array of `{ "name": "…", "sequence": "2.5" }`; sequence can be null |
| description | Nonempty string |
| genres | Array of unique nonblank names; complete replacement |
| publishedYear | Four-digit string or integer; normalized to string |

Ebook reports exclude narrators. Publisher, language, identifiers, edition,
abridgement, publication date, covers, chapters and tags are deferred write fields.
Files, paths, playback and bulk ABS mutation endpoints are never write targets.
Individual matching, providers and integrated AI are outside this PR.

## Failed writes and restart

The app records durable intent before each single-item CLI submission and never
automatically retries a write. Successful items stay successful; repeating an
identical approved proposal checks readback and skips an already applied item.
Known pre-submit failures/conflicts are reported individually. A submitted update
with uncertain outcome stops the batch.

Reconnect after restart, then click **Check ABS results**. This reads ABS only:
the intended state becomes verified; unchanged original state becomes not_applied
and can be selected with fresh confirmation. Mixed/unexpected state remains
uncertain and blocks writes. Unattempted items can be selected again. Re-export
for a conflicting snapshot; never edit the journal to bypass a guard.

`data/bulk` stores private snapshots and `writes.jsonl`. Preserve both, together
with the legacy database. No new database or legacy migration is introduced.
If `ABS_MM_DB` selects another database location, bulk files live in its sibling
`bulk` directory. The launcher and an already-running fallback both block writes
when that bulk journal contains uncertain operations.
Unresolved legacy journal entries block new writes; follow the existing
[recovery/backup instructions](../README.md#backup-restore-and-uncertain-writes)
in the legacy app. The new app does not resolve them. Stop both apps before
backup, copy the complete bulk directory, and use the existing SQLite backup
procedure for legacy data. Never restore an older journal to bypass an operation.

## First real metadata update — owner action required

The new app has been tested with synthetic adapters and loopback HTTP only.
No live ABS connection or production write was performed for this rebuild.

1. Back up ABS `/config` and `/metadata`, the legacy database and `data/bulk`.
2. Confirm no unresolved legacy operations. Read-only inspection during development
   found 142 verified and 3 skipped entries, with none unresolved; recheck current state.
3. Launch and connect. Export the real library and confirm its count and a few
   books' expanded metadata against ABS.
4. Create/import a proposal changing **one title on one audiobook**. Copy all
   identifiers and the before title from that export. Select only that field.
5. Inspect the exact preview, then explicitly authorize the update by typing its
   confirmation phrase and pressing Apply confirmed changes.
6. Require a verified readback result. Independently inspect the title in ABS and
   confirm subtitle, credits, series, cover, chapters, files and playback are preserved.
7. Reopen/import the same proposal and confirm a verified success is skipped rather
   than submitted again. Only then authorize a small bulk batch separately.

If any result is uncertain, use Check ABS results; do not submit again blindly.
Real-instance full-library export, the first update/readback, and a small approved
batch remain unverified acceptance criteria until the owner performs these steps.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests scripts/serve_bulk_fixture.py
.\.venv\Scripts\python.exe -m pip check
.\start.ps1 -Check
cd frontend
npm ci
npm run build
npm test
```

The browser test uses installed Edge, a synthetic 500-book loopback server, and
the production frontend bundle. It covers export/import, confirmation, readback,
ebook report download and absence of network calls for local interactions.
Measurements include browser automation and two animation frames. The final
Windows run (Edge 157.0.4341.42) measured 36 filtering, selection, exclusion, editing and pagination interactions:
p95 58.8 ms, maximum 146.6 ms, zero API calls during local interactions. This is
synthetic local responsiveness evidence, not a production-network performance claim.
Raw measurements/screenshots are generated under ignored `frontend/test-results`.

Local validation: full suite **519 passed, 4 skipped** in 2392.10 seconds on
Windows/Python 3.13.14. Three official-binary tests require a clean OS profile;
one private historical-export test is deliberately excluded. Final bulk and
legacy-writer/interlock checks: **40 passed**. One Starlette TestClient/httpx
deprecation warning remains in test tooling. Ruff, pip check, TypeScript/Vite
build, both launcher checks, actual Windows launcher HTTP smoke, and the
synthetic browser workflow passed. CI includes Python 3.12 validation; its remote
result is separate from these local checks. No production ABS request/write ran.
