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

If an audit is interrupted with Streamlit's Stop control, its partial snapshot is retained and cannot authorize writes. A process crash may leave it labelled `running`; start another audit to mark the old attempt interrupted. A write interrupted while the CLI is running remains prepared/applied/uncertain and must be reconciled before further writes. Never delete the database to bypass recovery checks.

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

Only `title`, `subtitle`, `authors`, `narrators`, and `series` are writable. The app never calls bulk-update, chapter, cover-mutation, file, embed-metadata, rename, or scan endpoints. Covers, chapters, tags, other metadata, and media structures are checked after each write. Playback progress is omitted from updates; user progress is not fetched or compared because it may change while listening. No M4B/EPUB is opened for writing. No AI or online metadata matching is used.

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

Adapter commands and shapes were checked against [abs-cli v1.2.0](https://github.com/thomaslazar/abs-cli/releases/tag/v1.2.0), its actual `--help-full` output, and tagged [ItemsService](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Services/ItemsService.cs) and [AbsApiClient](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Api/AbsApiClient.cs) source. Metadata writes use only `items update --id CURRENT_ID --stdin`, which maps to `PATCH /api/items/{id}/media`. Expanded reads and all write verification use `items get --id CURRENT_ID --expanded`. The only direct HTTP diagnostic is a read-only `/status` GET for the server version; there is no direct metadata write fallback.
