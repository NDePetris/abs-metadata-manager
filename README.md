# Audiobookshelf Metadata Manager

A local Windows desktop workspace using Python 3.12+, native Streamlit controls, SQLite, and abs-cli.

## Start

```powershell
.\start.ps1 -Install -InstallCli
.\start.ps1
```

The app binds to loopback at http://127.0.0.1:8501. No startup metadata requests or writes occur. Use Settings to connect, then select the library in the sidebar.

## Everyday workflow

1. **Import:** Refresh Library reads ABS inventory, without a filesystem scan. Resume Import retains checkpoints; restart retains history. The latest completed inventory is displayed automatically. A newer incomplete import still prevents confirmation and writes.
2. **Match & Verify:** select a book, compare saved and desired values, and confirm fields. Confirming unchanged values records acceptance. Confirming an edit stages a correction locally. Suggestions remain evidence until you confirm a target. Search reference sources executes one explicit lookup immediately. Reject wrong editions and switch providers without changing books. Human acceptance and independent verification remain separate.
3. **Changes:** review accumulated corrections grouped by book. Edit/discard pending targets, explicitly approve selected ABS fields, inspect the exact selected preview, and enter its confirmation phrase and acknowledgement. Calibre corrections are edited manually in Calibre; Validate Calibre Changes lists its catalog read-only. Calibre verification and subsequent ABS observation are separate states.
4. **History:** chronological imports and human decisions. Advanced sections expose historical snapshots and write journals. Uncertain operations remain prominently flagged. Reconcile reads live metadata without retrying writes. Rollback requires its own exact confirmation and unchanged applied state.
5. **Settings:** connection and Calibre paths. The library selection persists across pages and sessions.

File metadata and folder verification display **Not checked** unless reliable evidence exists. Missing file inspection is not itself an outstanding correction. ABS updates never establish file-embedded correctness.

## Existing local database

Stop the old app and back up `data/metadata.sqlite3` (or the path named by `ABS_MM_DB`). Start this version with the same database path. Schema changes are additive; snapshots, generic annotations, accepted decisions, corrections, and recovery journals remain. Do not delete the database to bypass recovery. Generic legacy annotations and proposals never become human acceptance automatically.

For a cautious first inspection, set `ABS_MM_DB` to a private copy of the database and launch disconnected. Review Import, Match & Verify, Changes, and History. Return to the original database for actual operation; a copied database has an independent lock, so do not operate both against the same server concurrently. No production write is necessary to inspect the redesign.

Fresh imports expire write approvals, while compatible human acceptance and accepted correction targets persist. Exact verified narrator corrections and library/UUID-bound Calibre identifier corrections preserve unrelated valid field acceptance. Unexpected identity/path changes reopen review. Old item IDs are never resolved to new write targets.

## Credentials and preferences

Nonsecret settings live in `%LOCALAPPDATA%\ABS Metadata Manager\settings.json`. API keys are stored in separate Windows user-scoped DPAPI-encrypted blobs, with no plaintext fallback and no additional credential dependency. The password input never displays the saved key. Forget saved API key deletes that server’s encrypted blob. Do not copy `.env` or personal abs-cli credentials into the repository; this UI no longer discovers tokens from them.

DPAPI protects against accidental file/Git disclosure. It does **not** reliably protect against an AI coding tool executing with the same Windows identity and sufficient access: that code may decrypt the key or access the application while it is using it. Isolate untrusted tooling under a separate restricted identity to create a meaningful boundary.

Credentials remain in process memory while used and enter the CLI through its child environment, never arguments. Automatic reconnection reconstructs saved settings; remote validation still requires an explicit action. Production development/testing must use synthetic credentials, separate settings directories, and fake or loopback transports.

## Safety and source authority

Only audiobook title, subtitle, authors, narrators, and series are writable through `items update --id CURRENT_ID --stdin`. Explicit field approval, exact digest confirmation, live identity/path/value guards, unrelated-media verification, FULL-synchronous pre-send journals, sequential execution, and the workspace lock remain mandatory. Prepared/applied/uncertain entries block further writes pending reconciliation. No automatic write retries occur.

abs-cli 1.2.0 remains the validated writer. A configured abs-cli refresh token blocks writes because upstream authentication refresh can retry PATCH. This app never removes that token for you. Playback progress, covers, chapters, paths, and files are not written.

Calibre remains authoritative for ebooks. Configure the local executable and library folder, then validate with the fixed `calibredb list --for-machine` command. Numeric IDs are scoped to that library; UUIDs and compatible book evidence prevent ambiguous verification. Close Calibre if local access is busy. No unlock, repair, database editing, EPUB editing, or folder mutation is attempted. Advanced catalog upload remains available. Custom columns and unsupported metadata remain manual.

Automatic audio tag rewriting, remuxing, direct file inspection, renaming, new providers, scheduling, and AI adjudication are deferred.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests
.\.venv\Scripts\python.exe -m pip check
.\start.ps1 -Check
```

Tests use synthetic databases, fake providers and adapters, and the local HTTP simulator. UI tests replace the credential boundary with a fake. See [validation evidence](docs/VALIDATION.md) and [production safeguards](AGENTS.md).
