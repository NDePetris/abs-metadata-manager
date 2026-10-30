# Audiobookshelf Metadata Manager

A local Windows desktop workspace for auditing Audiobookshelf metadata, reviewing individual field corrections, and applying only explicitly approved changes. Python 3.12+, Streamlit, SQLite, and the external `abs-cli` executable.

**No production metadata writes were performed during development.** The CLI transport has been verified against a loopback HTTP simulator using the official abs-cli 1.2.0 Windows binary. Compatibility with your actual server and account remains to be validated.

## Start on Windows

From PowerShell with Python 3.12 or newer installed:

```powershell
Set-Location G:\Projects\abs-metadata-manager
.\start.ps1 -Install -InstallCli
```

`-Install` installs the tested dependency versions into `.venv`. `-InstallCli` downloads the official **abs-cli 1.2.0** Windows release into `.tools` and verifies its published SHA-256 checksum. Omit `-InstallCli` to use your own executable. These flags are needed only on first setup or deliberate reinstallation.

On subsequent launches:

```powershell
.\start.ps1
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). The app binds to loopback, disables usage telemetry, and opens no production connection on startup. If local execution policy blocks the script, use a process-only invocation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

Check the launcher without starting a server with `.\start.ps1 -Check`; choose another local port with `.\start.ps1 -Port 8502`. Stop the app with Ctrl+C in its terminal.

For non-Windows Python environments, install with `python -m pip install -e '.[dev]'`, install the official abs-cli 1.2.0 for your OS, and run `python -m streamlit run app.py --server.address 127.0.0.1`. The Windows launcher and binary are the tested platform.

## Connect

Open **Settings** and enter your server URL, API key, and optional executable path. Select **Connect & check compatibility**, then select the audiobook library. The app shows the CLI version, server version, available libraries, and compatibility warnings. Credentials entered here remain in session memory.

Alternatively, copy `.env.example` to `.env` locally. Settings also uses `ABS_SERVER`, `ABS_TOKEN`, `ABS_LIBRARY`, and existing `~/.abs-cli/config.json`. `ABS_URL` is a legacy alias. Environment variables take precedence over the CLI config; entered settings take precedence for that connection. Tokens are passed through the subprocess environment, never command arguments or preview URLs.

**Important abs-cli 1.2.0 limitation:** upstream can retry a 401 PATCH without its request body after refreshing authentication. To enforce no automatic write retries, this app blocks writes if `~/.abs-cli/config.json` has a nonempty `refreshToken`. Read-only connection and audits still work. Before enabling writes, back up that config locally, use an Audiobookshelf API key in Settings or `ABS_TOKEN`, and remove the `refreshToken` property from the CLI config using a text editor. The app never modifies that property for you. Do not commit the config or backup. Expired credentials stop the batch and require reconciliation if a write was already submitted.

Only CLI version 1.2.0 is currently enabled for writes. Its declared tested ABS range is 2.37.0–2.37.1; servers outside that range display a warning. A successful connection is not proof of write compatibility. Follow the [live-validation checklist](docs/LIVE_VALIDATION.md).

## Everyday workflow

1. **Library:** choose **Audit whole library**. All pages and expanded book records are fetched sequentially, with a default 1.5 seconds between CLI calls. Allow a few minutes for 110 books. Progress appears during the operation. This is an audit, not an ABS filesystem scan. Search the results and inspect full observed JSON, normalized evidence, chapter names, and covers. Cover previews load only when explicitly requested.
2. **Review:** filter by field, confidence, and decision. Inspect the full current and proposed values, reason, folder evidence, and embedded tags exposed by ABS. Approve, reject, or edit a field and save its decision. Bulk approval acts only on the proposal IDs you deliberately select; no rows are preselected. For missing credits or a real subtitle, use **Create a manual field proposal**, then approve it separately. Contributor and series edits use JSON arrays, preserving multiple people and series.
3. **Apply:** inspect the exact before/after fields and CLI JSON payload for every book. Enter the batch-specific confirmation phrase, tick the explicit write checkbox, and press **Apply confirmed changes**. Live identity and all primary source values are revalidated before each book; approved fields are combined into one update per book. The app stops at the first unsafe or uncertain result.
4. **History:** inspect successful, skipped, failed, uncertain, and reverted operations. Reconciliation reads live metadata without resubmitting anything. Select a verified book operation to preview and explicitly confirm its guarded rollback.

Decisions and history survive app restarts. A new audit resets prior approvals, even if suggestions look identical. A partial or interrupted new audit disables applying the previous snapshot. Historical snapshots remain available for inspection. Already applied decisions are consumed; rollback never silently reapproves them.

If an audit is interrupted with Streamlit's Stop control, its partial snapshot is retained and cannot authorize writes. A process crash may leave it labelled `running`; **Resume audit** also handles that state once the process lock is released. A write interrupted while the CLI is running remains prepared/applied/uncertain and must be reconciled before further writes. Never delete the database to bypass recovery checks.

## Interrupted read-only audits

The Cloudflare tunnel remains the connection route; direct LAN access is not required. Library listing and expanded item reads retry only timeouts, connection resets, and identifiable HTTP 429/502/503/504 failures, up to **three total attempts**, with **1- and 2-second backoffs**. Authorization failures, other permanent errors, malformed JSON, and invalid schemas stop immediately. Metadata updates and rollback writes never receive automatic retries.

The request timeout defaults to **90 seconds per attempt**. Change **Settings → CLI timeout (seconds)** and reconnect to apply a different timeout, or set `ABS_MM_TIMEOUT=120` in your local environment before launch. Existing `.env` overrides (including an earlier value of 45) take precedence over the new default. Requests remain sequential and paced.

To resume an audit created by this version:

1. Restart with `.\start.ps1` if necessary, keeping the same SQLite database (`ABS_MM_DB`).
2. In **Settings**, connect using the same Cloudflare server URL and select the same library. The app never switches libraries automatically. A library name suggesting ebooks, or a snapshot containing mostly ebooks, displays a warning; ebooks remain read-only.
3. In **Library → Audit snapshot**, select the latest interrupted (or abandoned `running`) audit for that library. Check **Saved progress** and **Latest audit error**.
4. Choose **Resume audit**. The app re-enumerates all library pages, compares the complete ID/identity/path manifest and library folders with the saved checkpoint, and validates every saved book before reuse. Validated saved books skip expanded `items get` calls. The same snapshot and pending findings continue without duplicating books or expiring decisions again. A new **Audit whole library** instead creates a separate snapshot and expires prior approvals.
5. If another request exhausts retries, repeat Resume later. Progress and request timings survive app restarts. Changed membership, identity, or paths refuse resumption and require a new audit; the reason stays visible. Only the latest complete snapshot can authorize approvals or writes, after every expected book is saved and a final membership check succeeds.

**The two audits interrupted before this update (Audiobooks ~51/110 and Books ~56/656) have no original membership checkpoint.** Their saved records and findings are preserved, but unchanged membership cannot be proven retrospectively. Resume is disabled with that explanation. For each, select its original library in Settings and choose **Audit whole library** once to establish a checkpoint. If that new audit is interrupted, resume it using the steps above. Do not delete old snapshots, edit database statuses, copy approvals, or infer a new target from `oldLibraryItemId` to bypass this check. If listing fails before any books are saved, Resume safely retries establishing the initial checkpoint.

During audits, a native status message shows the operation, current item/page, attempt, duration, exit code, and diagnostic category. **Audit diagnostics · slowest item requests** retains the ten slowest expanded request attempts, including failures, for each snapshot. A timeout in a GET is reported as read-only and does not claim that an update may have been submitted.

Rotating diagnostics live at `data/audit.log` by default (1 MB each, current file plus three backups). With `ABS_MM_DB`, the log is beside that database. Progress, failures, and per-attempt CLI timings are recorded without response bodies, command environments, API keys, URLs, or headers. Stderr is reduced to recognized HTTP status and connection-error vocabulary; arbitrary stderr is withheld rather than risking secret disclosure. These logs, like the database, are local and ignored by Git.

## Recovery and rollback

Each write commits a FULL-synchronous SQLite journal entry containing the live pre-update item, identity, expected before/after values, exact payload, and proposal IDs **before** invoking the CLI. States are `prepared → applied → verified`; exceptions or failed verification become `uncertain`. A crash can leave either earlier state; both block new writes just like uncertain entries.

In **History**, choose **Reconcile using live metadata (read-only)**:

- Matching intended values and unchanged unrelated metadata become `verified`.
- Matching the original state becomes `not_applied`. Proposals return to pending; any retry requires deliberate approval and a new confirmation.
- Mixed values, changed identity/path, or unrelated changes remain unresolved. Inspect the book directly in Audiobookshelf. Deliberately restore a known recorded state there, then reconcile again. There is no force/ignore-uncertain button.

Rollback is selective by verified book operation. It restores only that operation's fields, after checking that **all primary live fields and unrelated metadata** still match the recorded applied state. It has its own exact preview, confirmation, journal, verification, and reconciliation. Outside edits cause a refusal. A rollback can be declined safely; it never retries itself. Keep the SQLite database as the authoritative rollback record. This journal is not a full Audiobookshelf backup.

## Rules and boundaries

The deterministic audit separates observed JSON, analytical normalized values, and proposed targets. Confidence never authorizes a write. Folder names and embedded tags are evidence, not absolute truth.

Rules cover explicit series-number title suffixes/prefixes, terminal `(Unabridged)` labels, decimal positions, Dresden Files spelling, Sarah J. Maas initials, and corroborated Harry Potter role inversions. A conflicting existing series position or narrator credit is retained for investigation. Legitimate subtitles and punctuation survive. Generic chapter labels are informational. Two recordings with the same title remain separate items.

Only `title`, `subtitle`, `authors`, `narrators`, and `series` are writable. The app never calls bulk-update, chapter, cover-mutation, file, embed-metadata, rename, or scan endpoints. Covers, chapters, tags, other metadata, and media structures are checked after each write. Playback progress is omitted from updates; user progress is not fetched or compared because it may change while listening. No M4B/EPUB is opened for writing. No AI is invoked. Independent online lookups run only through explicit actions in Verification and cannot approve or apply changes.

Ebooks may be inventoried alongside audiobooks or in a selected book library; applying ebook corrections is refused. **Calibre remains authoritative.** There is no Calibre or filesystem synchronization.

Read-only v2.1 audit imports and JSON/CSV finding exports are available. An imported ID can never authorize a live write; start a fresh live audit. The previous project's approvals and journal are never imported. You can load six synthetic read-only examples without credentials from the empty Library view.

## Development and testing

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests
.\start.ps1 -Check
```

Tests use fake adapters and a local HTTP simulator. No test contacts a production server. The official CLI transport smoke test runs when `.tools/abs-cli.exe` is present and the CLI config has no refresh token; otherwise it skips. The optional real-export regression runs when `tests/fixtures/private/audit_snapshot.json` exists. Keep that private file ignored. Checked-in synthetic regression tests cover the named real problem cases without preserving private inventory IDs or paths. Streamlit tests require loopback socket access on Windows.

See [validation results](docs/VALIDATION.md), [live checklist](docs/LIVE_VALIDATION.md), and [architectural rules](AGENTS.md).

## Local data and limitations

- Private SQLite data defaults to `data/metadata.sqlite3`; override with `ABS_MM_DB`. Back up this file while the app is stopped. Exports, journals, credentials, `.venv`, and the downloaded CLI are excluded from Git.
- One OS-held lock serializes audits, reviews, writes, and reconciliation for this database across app sessions/processes. Use one database per server/workspace; deliberately running independent databases bypasses that shared operation lock.
- ABS has no atomic compare-and-swap for these metadata updates. The app reads immediately before writing and verifies immediately after. Avoid editing the same records elsewhere during a batch. Unexpected changes stop the batch.
- Pagination rejects duplicate IDs, changing totals, and incomplete results. Same-count concurrent library replacement cannot be detected perfectly; run audits while imports and folder moves are idle. Write-time checks still reject stale identities and source values.
- Missing/ambiguous metadata requires your own edition evidence. Only cached embedded tags exposed by ABS are inspected; the app does not probe audio files. Malformed or incomplete expanded schemas fail closed.
- Unrelated media changes (including changed cached tags or chapter structure) can conservatively leave an operation uncertain. Resolve in History; there is no unsafe override.
- The UI is designed for desktop. Wide tables scroll horizontally; the field inspector and exact payload expose full values.

## Upstream references

Adapter commands and shapes were checked against [abs-cli v1.2.0](https://github.com/thomaslazar/abs-cli/releases/tag/v1.2.0), its actual `--help-full` output, and tagged [ItemsService](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Services/ItemsService.cs) and [AbsApiClient](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Api/AbsApiClient.cs) source. Metadata writes use only `items update --id CURRENT_ID --stdin`, which maps to `PATCH /api/items/{id}/media`. Expanded reads and all write verification use `items get --id CURRENT_ID --expanded`. Direct HTTP is restricted to the `/status` diagnostic and documented public bibliographic GETs; there is no direct metadata write fallback.

## Full-library verification

Open **Verification** and select a completed audit snapshot. Every book appears, including books with no audit findings. The workspace contains field coverage, a searchable status matrix, embedded/folder evidence, candidate comparisons, source links, manual review evidence, and complete JSON/CSV downloads. Downloads always include all books regardless of filters. Existing snapshots can be assessed offline without a new server request.

For online evidence, connect in Settings to the snapshot's original Cloudflare server URL. Expand **Independent online lookups**, select **Discover server metadata providers**, choose one of the returned IDs (Audible/Audnexus are prioritized), select one book, selected books, or all audiobooks, then **Prepare online verification run**. Preparation is local; **Start / resume read-only lookups** performs sequential searches. Increase **Maximum books this action** to 110 to process an entire audiobook collection in one action, or use the default 10 for frequent pause opportunities. Progress and each response are persisted. Cancel affects pending requests at the next book boundary; an in-flight request may finish. Streamlit Stop/interruption retains already saved responses. Resume never re-queries attached completed records. Failed records require the explicit retry checkbox; a provider outage pauses the remaining queue.

Open Library supports targeted, human-initiated single-book searches only. Google Books is optional and requires `GOOGLE_BOOKS_API_KEY` in your ignored `.env`; routine operation needs no AI account or paid service. Queries disclose the selected book's title/author or available identifiers to the source. Responses are cached with source, query, timestamp, and raw evidence. They stay available offline; refresh is an explicit action. Source selection and known limitations are documented in [Verification guide](docs/VERIFICATION.md).

Human review dates, evidence, and outcomes (including confirmed error versus formatting preference) are independent of the external status. Selecting a plausible edition does not turn ambiguity into automatic external verification. A supported, corroborated recording discrepancy can become a **pending** proposal through **Create pending correction in Review**. Review approval and exact Apply confirmation remain mandatory. Ebooks and unsupported fields remain read-only.

Calibre catalog input accepts a JSON file created on your Calibre machine using the read-only `calibredb list --for-machine --fields title,authors,series,series_index,identifiers` command. See the guide for an exact PowerShell export command. The app never opens Calibre's database or modifies managed folders/EPUBs. Without a unique catalog match, ebook assessment explicitly states its limited coverage.
