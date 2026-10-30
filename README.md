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

## Existing database and migration

Stop the app and back up your existing SQLite database before starting the updated version. Its default is `data/metadata.sqlite3`; if `ABS_MM_DB` is set, back up that database instead. Keep the backup private. Start the app with the same database path: schema changes are additive, and existing snapshots, annotations, provider evidence, write journal, and rollback history are retained. Existing generic reviews are not converted to field acceptance. Select your original server and library in Settings and inspect a completed historical snapshot, or start a new import to establish current inventory. A fresh import expires old write approvals; retained field decisions do not approve writes.

If an old interrupted audit has no membership checkpoint, its history is still available but it cannot be resumed safely; start a fresh import. Do not delete or reset the database to resolve an interrupted operation.

## Connect

Open **Settings** and enter your server URL, API key, and optional executable path. Select **Connect & check compatibility**, then select the audiobook library. The app shows the CLI version, server version, available libraries, and compatibility warnings. Credentials entered here remain in session memory.

Alternatively, copy `.env.example` to `.env` locally. Settings also uses `ABS_SERVER`, `ABS_TOKEN`, `ABS_LIBRARY`, and existing `~/.abs-cli/config.json`. `ABS_URL` is a legacy alias. Environment variables take precedence over the CLI config; entered settings take precedence for that connection. Tokens are passed through the subprocess environment, never command arguments or preview URLs.

**Important abs-cli 1.2.0 limitation:** upstream can retry a 401 PATCH without its request body after refreshing authentication. To enforce no automatic write retries, this app blocks writes if `~/.abs-cli/config.json` has a nonempty `refreshToken`. Read-only connection and audits still work. Before enabling writes, back up that config locally, use an Audiobookshelf API key in Settings or `ABS_TOKEN`, and remove the `refreshToken` property from the CLI config using a text editor. The app never modifies that property for you. Do not commit the config or backup. Expired credentials stop the batch and require reconciliation if a write was already submitted.

Only CLI version 1.2.0 is currently enabled for writes. Its declared tested ABS range is 2.37.0–2.37.1; servers outside that range display a warning. A successful connection is not proof of write compatibility. Follow the [live-validation checklist](docs/LIVE_VALIDATION.md).

## Everyday workflow

1. **Import:** connect in Settings and select a library, then import its ABS inventory. Import reads the saved server metadata; it does not search external catalogs or scan files. Review the import summary, including additions, removals, and changes. Resume an interrupted import when its checkpoint allows it.
2. **Match & Verify:** explicitly discover the server's providers, then search for a book and compare candidates. Check recording identifiers, narrators, duration, language and abridgment as well as title and author. Reject wrong recordings and record evidence for your edition. A work match alone does not establish recording identity; a catalog may not contain your edition.
3. **Resolve:** inspect saved values, candidate evidence, and existing decisions. Confirm fields that are correct, mark fields needing correction, or leave them unresolved. Human confirmation is a separate record from independent external verification and is never permission to write.
4. **Update Sources:** for audiobook fields supported by the writer, prepare and individually approve corrections, inspect the exact batch preview, and explicitly confirm the write. For ebooks, edit the authoritative Calibre library yourself and import a fresh Calibre catalog for comparison. Covers, chapters, embedded tags, files, and other unsupported fields require manual action.
5. **Reconcile & History:** inspect the durable write journal and reconcile interrupted or uncertain operations using read-only live checks. Import the library again after source changes and review changed or unresolved fields. A claim that a Calibre edit was made does not itself verify the edit; check it against a subsequent catalog import and separately inspect ABS after synchronization.

A saved field acceptance can persist across imports when the same server, library, item identity, edition evidence and field value remain compatible. A changed value or materially changed identity reopens review; ambiguous moves or new IDs do not silently inherit acceptance. Disagreement from a newly queried catalog is retained as evidence rather than automatically revoking human acceptance. Existing generic review annotations are history, not field acceptance. New imports expire previous production write approvals even when the same human acceptance persists. Historical snapshots and journal entries remain available.

A partial or interrupted import cannot authorize writes. A crash may leave it labelled `running`; resume it once the process lock is released. A write interrupted while the CLI is running remains prepared/applied/uncertain and must be reconciled before further writes. Never delete the database to bypass recovery checks.

## Interrupted read-only audits

The Cloudflare tunnel remains the connection route; direct LAN access is not required. Library listing and expanded item reads retry only timeouts, connection resets, and identifiable HTTP 429/502/503/504 failures, up to **three total attempts**, with **1- and 2-second backoffs**. Authorization failures, other permanent errors, malformed JSON, and invalid schemas stop immediately. Metadata updates and rollback writes never receive automatic retries.

The request timeout defaults to **90 seconds per attempt**. Change **Settings → CLI timeout (seconds)** and reconnect to apply a different timeout, or set `ABS_MM_TIMEOUT=120` in your local environment before launch. Existing `.env` overrides (including an earlier value of 45) take precedence over the new default. Requests remain sequential and paced.

To resume an audit created by this version:

1. Restart with `.\start.ps1` if necessary, keeping the same SQLite database (`ABS_MM_DB`).
2. In **Settings**, connect using the same Cloudflare server URL and select the same library. The app never switches libraries automatically. A library name suggesting ebooks, or a snapshot containing mostly ebooks, displays a warning; ebooks remain read-only.
3. In **Import → Audit snapshot**, select the latest interrupted (or abandoned `running`) audit for that library. Check **Saved progress** and **Latest audit error**.
4. Choose **Resume audit**. The app re-enumerates all library pages, compares the complete ID/identity/path manifest and library folders with the saved checkpoint, and validates every saved book before reuse. Validated saved books skip expanded `items get` calls. The same snapshot and pending findings continue without duplicating books or expiring decisions again. A new **Audit whole library** instead creates a separate snapshot and expires prior approvals.
5. If another request exhausts retries, repeat Resume later. Progress and request timings survive app restarts. Changed membership, identity, or paths refuse resumption and require a new audit; the reason stays visible. Only the latest complete snapshot can authorize approvals or writes, after every expected book is saved and a final membership check succeeds.

**The two audits interrupted before this update (Audiobooks ~51/110 and Books ~56/656) have no original membership checkpoint.** Their saved records and findings are preserved, but unchanged membership cannot be proven retrospectively. Resume is disabled with that explanation. For each, select its original library in Settings and choose **Audit whole library** once to establish a checkpoint. If that new audit is interrupted, resume it using the steps above. Do not delete old snapshots, edit database statuses, copy approvals, or infer a new target from `oldLibraryItemId` to bypass this check. If listing fails before any books are saved, Resume safely retries establishing the initial checkpoint.

During audits, a native status message shows the operation, current item/page, attempt, duration, exit code, and diagnostic category. **Audit diagnostics · slowest item requests** retains the ten slowest expanded request attempts, including failures, for each snapshot. A timeout in a GET is reported as read-only and does not claim that an update may have been submitted.

Rotating diagnostics live at `data/audit.log` by default (1 MB each, current file plus three backups). With `ABS_MM_DB`, the log is beside that database. Progress, failures, and per-attempt CLI timings are recorded without response bodies, command environments, API keys, URLs, or headers. Stderr is reduced to recognized HTTP status and connection-error vocabulary; arbitrary stderr is withheld rather than risking secret disclosure. These logs, like the database, are local and ignored by Git.

## Recovery and rollback

Each write commits a FULL-synchronous SQLite journal entry containing the live pre-update item, identity, expected before/after values, exact payload, and proposal IDs **before** invoking the CLI. States are `prepared → applied → verified`; exceptions or failed verification become `uncertain`. A crash can leave either earlier state; both block new writes just like uncertain entries.

In **Reconcile & History**, choose **Reconcile using live metadata (read-only)**:

- Matching intended values and unchanged unrelated metadata become `verified`.
- Matching the original state becomes `not_applied`. Proposals return to pending; any retry requires deliberate approval and a new confirmation.
- Mixed values, changed identity/path, or unrelated changes remain unresolved. Inspect the book directly in Audiobookshelf. Deliberately restore a known recorded state there, then reconcile again. There is no force/ignore-uncertain button.

Rollback is selective by verified book operation. It restores only that operation's fields, after checking that **all primary live fields and unrelated metadata** still match the recorded applied state. It has its own exact preview, confirmation, journal, verification, and reconciliation. Outside edits cause a refusal. A rollback can be declined safely; it never retries itself. Keep the SQLite database as the authoritative rollback record. This journal is not a full Audiobookshelf backup.

## Rules and boundaries

The deterministic audit separates observed JSON, analytical normalized values, and proposed targets. Confidence never authorizes a write. Folder names and embedded tags are evidence, not absolute truth.

Rules cover explicit series-number title suffixes/prefixes, terminal `(Unabridged)` labels, decimal positions, Dresden Files spelling, Sarah J. Maas initials, and corroborated Harry Potter role inversions. A conflicting existing series position or narrator credit is retained for investigation. Legitimate subtitles and punctuation survive. Generic chapter labels are informational. Two recordings with the same title remain separate items.

Only `title`, `subtitle`, `authors`, `narrators`, and `series` are writable. The app never calls bulk-update, chapter, cover-mutation, file, embed-metadata, rename, or scan endpoints. Covers, chapters, tags, other metadata, and media structures are checked after each write. Playback progress is omitted from updates; user progress is not fetched or compared because it may change while listening. No M4B/EPUB is opened for writing. No AI is invoked. Independent online lookups run only through explicit actions in Match & Verify and cannot approve or apply changes.

Ebooks may be inventoried alongside audiobooks or in a selected book library; applying ebook corrections is refused. **Calibre remains authoritative.** There is no Calibre or filesystem synchronization.

Read-only v2.1 audit imports and JSON/CSV finding exports are available. An imported ID can never authorize a live write; start a fresh live audit. The previous project's approvals and journal are never imported. You can load six synthetic read-only examples without credentials from the empty Import view.

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
- Unrelated media changes (including changed cached tags or chapter structure) can conservatively leave an operation uncertain. Resolve in Reconcile & History; there is no unsafe override.
- The UI is designed for desktop. Wide tables scroll horizontally; the field inspector and exact payload expose full values.

## Upstream references

Adapter commands and shapes were checked against [abs-cli v1.2.0](https://github.com/thomaslazar/abs-cli/releases/tag/v1.2.0), its actual `--help-full` output, and tagged [ItemsService](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Services/ItemsService.cs) and [AbsApiClient](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Api/AbsApiClient.cs) source. Metadata writes use only `items update --id CURRENT_ID --stdin`, which maps to `PATCH /api/items/{id}/media`. Expanded reads and all write verification use `items get --id CURRENT_ID --expanded`. Direct HTTP is restricted to the `/status` diagnostic and documented public bibliographic GETs; there is no direct metadata write fallback.

## Full-library verification

Completed snapshots can be inspected offline. External evidence is fetched only through explicit provider discovery and lookup actions. Server provider availability and catalog fields vary; search requests disclose the selected title, author, or identifiers to the provider. Responses and their source evidence are retained locally. Cached results can be reused; refresh is deliberate. See the [verification guide](docs/VERIFICATION.md) for matching, provider limits, and the read-only Calibre catalog export.

Human acceptance, independent external verification, correction proposals, and write approvals are distinct. A selected candidate or a catalog disagreement never authorizes a remote change. Only the current complete live audiobook snapshot can supply separately approved proposals for an explicitly confirmed write.
