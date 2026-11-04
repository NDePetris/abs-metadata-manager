# Audiobookshelf Metadata Manager

A local Windows desktop workspace using Python 3.12+, native Streamlit controls, SQLite, and abs-cli.

See [PRODUCT.md](PRODUCT.md) for Phase 1 goals, [DESIGN.md](DESIGN.md) for current
and planned workflows, and [AGENTS.md](AGENTS.md) for implementation boundaries.

## Start

```powershell
.\start.ps1 -Install -InstallCli
.\start.ps1
```

The app binds to loopback at http://127.0.0.1:8501. No startup metadata requests or writes occur. Use Settings to connect, then select the library in the sidebar.

## Everyday workflow

Match & Verify now starts with **quality exceptions**, not a requirement to accept
every field. Independent evidence and Calibre authority can establish Pass without
manual confirmation. Use Quality / Queue filters to find attention/evidence gaps,
or All books to revisit passing values. Essential bibliographic progress is separate
from artwork and optional categorization. Cover gallery intentionally retrieves up
to 12 images and confirms only individually inspected artwork; genre/tag hygiene
records exact-value library preferences. Make artwork and category corrections in
native ABS/Calibre. See [S3 results, measurements and limitations](docs/S3_REVIEW.md).

1. **Import:** Refresh Library reads ABS inventory, without a filesystem scan. Resume Import retains checkpoints; restart retains history. The latest completed inventory is displayed automatically. A newer incomplete import still prevents confirmation and writes.
2. **Match & Verify:** use the searchable Book selector or Previous/Next. Compare **Current in ABS**, **Reference**, and editable **Desired** values in one grid. Current is the imported snapshot, not a live read. Fields begin unselected; editing, Use reference, and Restore current select just that field. Select all includes applicable fields only. **Confirm** saves selected targets locally; **Confirm & Next** advances only after successful atomic confirmation. Unchanged values become human acceptance; changed values enter Changes without write approval. Native name editors retain individual contributors/order; series positions accept decimal text. More review actions contains notes, Reopen Selected and Review Later.

   Drafts survive reruns, reference actions, page/book navigation, and provider changes for the exact server/library/import/book. A new import starts fresh drafts, restoring accepted pending corrections before imported values. Drafts live in the current browser session, not across closing/reloading the browser. Copying never confirms or stages a value; missing provider data says Not supplied and cannot clear metadata. A displayed candidate is not an accepted edition. Search / Refine executes a lookup only when Search or Search another source is clicked. Work-only references cannot establish recording credits. Advanced / Evidence contains snapshot dates, raw evidence, rejected candidates and source synchronization details.
3. **Changes:** review accumulated corrections grouped by book. Edit/discard pending targets, explicitly approve selected ABS fields, inspect the exact selected preview, and enter its confirmation phrase and acknowledgement. Calibre corrections are edited manually in Calibre; Validate Calibre Changes lists its catalog read-only. Calibre verification and subsequent ABS observation are separate states.
4. **History:** chronological imports and human decisions. Advanced sections expose historical snapshots and write journals. Uncertain operations remain prominently flagged. Reconcile reads live metadata without retrying writes. Rollback requires its own exact confirmation and unchanged applied state.
5. **Settings:** connection and Calibre paths. The library selection persists across pages and sessions.

File metadata and folder verification display **Not checked** unless reliable evidence exists. Missing file inspection is not itself an outstanding correction. ABS updates never establish file-embedded correctness.

## Existing local database

Stop the old app and follow [Backup, restore and uncertain writes](#backup-restore-and-uncertain-writes) for `data/metadata.sqlite3` (or `ABS_MM_DB`). Start this version with the same database path. Schema changes are additive; snapshots, generic annotations, accepted decisions, corrections, and recovery journals remain. Do not delete the database to bypass recovery. Generic legacy annotations and proposals never become human acceptance automatically.

For a cautious first inspection, set `ABS_MM_DB` to a private copy and `ABS_MM_SETTINGS` to a new private directory. Copy only the nonsecret `settings.json` into that directory (not any `.dpapi` files) before launching; this retains the server/library context while preventing saved-key reconnection. Review Import, Match & Verify, Changes, and History. Return to the original database for actual operation; a copied database has an independent lock, so do not operate both against the same server concurrently. No production write is necessary to inspect the redesign.

Fresh imports expire write approvals, while compatible human acceptance and accepted correction targets persist. Exact verified narrator corrections and library/UUID-bound Calibre identifier corrections preserve unrelated valid field acceptance. Unexpected identity/path changes reopen review. Old item IDs are never resolved to new write targets.

## Credentials and preferences

Nonsecret settings live in `%LOCALAPPDATA%\ABS Metadata Manager\settings.json`. API keys are stored in separate Windows user-scoped DPAPI-encrypted blobs, with no plaintext fallback and no additional credential dependency. The password input never displays the saved key. Forget saved API key deletes that server’s encrypted blob. Do not copy `.env` or personal abs-cli credentials into the repository; this UI no longer discovers tokens from them.

DPAPI protects against accidental file/Git disclosure. It does **not** reliably protect against an AI coding tool executing with the same Windows identity and sufficient access: that code may decrypt the key or access the application while it is using it. Isolate untrusted tooling under a separate restricted identity to create a meaningful boundary.

Credentials remain in process memory while used and enter the CLI through its child environment, never arguments. Automatic reconnection reconstructs valid saved settings; remote validation still requires an explicit action. Missing, inaccessible, corrupt or undecryptable saved keys leave startup usable and show a recovery message. In Settings, enter a new key to reconnect (and replace the encrypted blob), or choose Forget saved API key. Error messages withhold key contents; persistence never falls back to plaintext. Production development/testing must use synthetic credentials, separate settings directories, and fake or loopback transports.

## Safety and source authority

Only audiobook title, subtitle, authors, narrators, and series are writable through `items update --id CURRENT_ID --stdin`. Explicit field approval, exact digest confirmation, live identity/path/value guards, unrelated-media verification, FULL-synchronous pre-send journals, sequential execution, and the workspace lock remain mandatory. Prepared/applied/uncertain entries block further writes pending reconciliation. No automatic write retries occur.

abs-cli 1.2.0 remains the validated writer. A configured abs-cli refresh token blocks writes because upstream authentication refresh can retry PATCH. This app never removes that token for you. Playback progress, covers, chapters, paths, and files are not written.

Calibre remains authoritative for ebooks. Configure the local executable and library folder, then validate with the fixed `calibredb list --for-machine` command. Numeric IDs are scoped to that library. Persistent library/ID/UUID bindings and corroborating folder/identifier plus work evidence prevent ambiguous verification. Accepted title and author targets are valid corroboration, including when both changed before the first comparison. Contradictory or ambiguous records remain unverified. For an initial ambiguous binding, use Changes / Advanced catalog input to explicitly associate the ebook with a catalog ID and UUID in the exact library context. Unscoped exports are archived evidence only; they cannot associate books or verify fields/corrections. Ebook review excludes narrators. Close Calibre if local access is busy. No unlock, repair, database editing, EPUB editing, or folder mutation is attempted. Advanced catalog upload remains available. Custom columns and unsupported metadata remain manual.

Automatic audio tag rewriting, remuxing, direct file inspection, renaming, new providers, scheduling, and AI adjudication are deferred.

## Validation

Fresh development environment (the launcher uses the same locked dependencies):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt -e '.[dev]'
```

```powershell
.\.venv\Scripts\python.exe -m pytest -ra --durations=10
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests
.\.venv\Scripts\python.exe -m pip check
.\start.ps1 -Check
git diff --check
```

Tests use synthetic databases, fake providers and adapters, and the local HTTP simulator. UI tests replace the credential boundary with a fake. See [validation evidence](docs/VALIDATION.md) and [production safeguards](AGENTS.md).

CI runs on PRs and pushes to main. Windows/Python 3.12 runs the full suite, Ruff,
`pip check` and `start.ps1 -Check`; Linux/Python 3.12 runs focused transport,
audit, guarded-write, locking and recovery regressions plus `pip check`. Prior
full Windows runs took about 11–15 minutes, so Linux does not duplicate all
Streamlit UI tests. No services, production secrets, CLI download or Calibre
installation are needed. Both jobs install `requirements.lock.txt` with `.[dev]`;
pip resolution and `pip check` catch inconsistent declarations.

The harness clears inherited ABS/provider/Calibre/proxy environment settings,
sets temporary Python home, app database/settings/local-app-data and netrc paths,
replaces credential and CLI-config readers, and blocks Python DNS/connect/connect_ex outside
loopback. This is a regression guard, not an OS sandbox for arbitrary subprocesses.
Official CLI tests supply only synthetic tokens and loopback URLs and refuse to
execute if the actual OS profile contains CLI config; Windows' known profile
folder cannot safely be redirected by changing HOME/USERPROFILE.

`-ra` reports skipped tests; JUnit artifacts retain their reasons. Without the
official binary, the three `test_official_cli_*` tests in `test_adapter.py` skip:
provider/search GET contract, apply/readback/rollback simulator, and transient
read/resume protocol. Fake-adapter and direct-GET simulator tests still run.
The historical private-export test is excluded by default even if a local file
exists; its separate explicit opt-in is `ABS_MM_TEST_PRIVATE_EXPORT=1` and is
never enabled in CI. Skips are coverage gaps, not passes. Do not claim Actions
passed from local results. Recommend requiring both stable CI job checks in main
branch protection after the first successful run; this PR does not configure it.


To exercise the desktop fixtures without credentials or external requests:

```powershell
.\.venv\Scripts\python.exe -m streamlit run scripts/visual_fixture.py --server.port 8505
```

This harness uses only a fake ABS adapter, fake provider responses and `.test-tmp-compact` storage. See [compact-screen acceptance evidence](docs/COMPACT_REVIEW.md).

## Backup, restore and uncertain writes

Stop every app process using the workspace before backup or restore. Wait for any
submitted operation to stop; interruption can leave durable intent requiring
reconciliation. Retain the entire database, not just snapshots: it also contains
decisions, evidence, approvals and write/rollback journals. The OS lock releases
on process exit; it does not resolve journal state.

Use SQLite's backup API to produce a consistent single-file copy. It includes
committed WAL contents; copying only a live `.sqlite3` file can omit them. Do not
delete `-wal`, `-shm` or `-journal` files. For a raw filesystem archive, stop all
users and copy the database and every present sidecar as one set. A SHM file is
coordination state, not a substitute for the WAL. Do not mix sets or copy them
independently while the database is active.

Example in PowerShell, with a **new private backup destination**. Substitute the
actual source if `ABS_MM_DB` is configured. This opens the source read-only,
refuses an existing destination and verifies the resulting backup without Store
initialization or remote requests:

```powershell
@'
import sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
target.parent.mkdir(parents=True, exist_ok=True)
with target.open('xb'):
    pass
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute('PRAGMA integrity_check').fetchall()
        if result != [('ok',)]:
            raise RuntimeError(result)
print('Consistent backup verified:', target)
'@ | .\.venv\Scripts\python.exe - 'data\metadata.sqlite3' 'data\backups\before-s1.sqlite3'
```

Also archive nonsecret `settings.json` from `ABS_MM_SETTINGS` or
`%LOCALAPPDATA%\ABS Metadata Manager`. Record the original database path and app
revision. Credentials are separate `.dpapi` blobs: retain them only in a protected
same-user Windows backup if desired, or deliberately omit and re-enter the key.
They are not portable credentials; never decrypt/export plaintext or commit any
backup. Local DB backup does not replace backups of ABS `/config` and `/metadata`
or the authoritative Calibre library.

Restore while stopped. First preserve the current database and sidecars as a
separate recovery set, even if damaged or uncertain. Verify the backup using the
integrity check above (or read-only `PRAGMA integrity_check`). Copy the verified
single-file API backup to a **new, unused** database path with no stale sidecars,
then point `ABS_MM_DB` there before launching. For example:

```powershell
$restoredDb = Join-Path $PWD 'data\restored-s1.sqlite3'
if (Test-Path -LiteralPath $restoredDb) { throw 'Choose an unused restore path.' }
Copy-Item -LiteralPath 'data\backups\before-s1.sqlite3' -Destination $restoredDb
$env:ABS_MM_DB = $restoredDb
.\start.ps1
```

Keep only one active workspace against the server. Restore matching settings
separately or reconnect explicitly. Inspect History, decisions and journals before
any operation. A backup preserves history only up to its capture time: never use
an older copy to bypass a later uncertain write. If later journals/decisions exist,
retain the newer recovery set and resolve that history first; do not merge SQLite
tables manually. If the latest intent is unavailable, stop and report the gap.

Prepared/applied/uncertain operations require **History → Reconcile using live
metadata (read-only)** against the original server. The workflow guards identity,
library/path, all managed values and unrelated metadata/media. Intended after-state
becomes verified; exact before-state becomes not_applied and resets its approval;
mixed/unexpected state remains uncertain and blocks writes. A failed readback stays
unresolved. Reconciliation updates the local journal but never resubmits the write.
Any later correction needs deliberate approval and a fresh preview/confirmation;
rollback is also a separately confirmed conditional write.

A successful CLI submission/exit is not end-to-end verification. Require guarded
readback and the operator's ABS check, including unrelated state. The last recorded
production incident is **operation 25**, an approved empty subtitle returned as
null. A scoped equivalence fix exists, but the operation remains uncertain in the
recorded evidence; S1 does not inspect or resolve production state. Follow
[the incident recovery steps](docs/PRODUCTION_FIXES.md#existing-database-and-operation-25)
and [live validation](docs/LIVE_VALIDATION.md). Complete reconciliation before
considering any separately authorized live write validation.
